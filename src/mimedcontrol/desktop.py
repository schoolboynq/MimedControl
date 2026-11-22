import os
import sys
from collections import deque
from queue import Empty, SimpleQueue
from time import monotonic

from . import monitors_unix, monitors_windows
from .displays import DisplayLayout, clamp
from .monitors_windows import configure_dpi

PAUSE_SHORTCUT = "Ctrl+Alt+Space"
PAD_SHORTCUT = "Ctrl+Alt+N"
CLICK_HOLD = 0.060
CLICK_GAP = 0.034
CLICK_QUEUE_LIFETIME = 0.25


def open_desktop(width, height):
    if sys.platform == "linux":
        return X11Desktop(width, height)
    return PynputDesktop(width, height)


class ClickDelivery:
    def __init__(self, set_button, move, position):
        self.set_button = set_button
        self.on_press = None
        self.move_pointer = move
        self.pointer_position = position
        self.pending = deque()
        self.deferred_pointer = None
        self.pressed_button = None
        # A held button stays down until release_hold(): the pointer drags while it moves.
        self.held = False
        self.transition_at = 0.0

    def move(self, pointer):
        # Keep both edges of an injected click at the same target, even on a fast re-grip.
        if self.pressed_button is not None and not self.held:
            self.deferred_pointer = pointer
        else:
            self.move_pointer(pointer)

    def click(self, button="left", *, target=None):
        self.queue_or_press("click", button, target)

    def hold(self, button="left", *, target=None):
        """Press a button and keep it down until release_hold(): a click if let go on the spot, else a drag."""
        self.queue_or_press("hold", button, target)

    def queue_or_press(self, kind, button, target):
        if button not in ("left", "right"):
            raise ValueError(f"Unsupported mouse button {button!r}")
        now = monotonic()
        if self.pressed_button is not None or self.pending or now < self.transition_at:
            if target is None:
                target = self.deferred_pointer if self.deferred_pointer is not None else self.pointer_position()
            self.pending.append((now, kind, button, target))
        else:
            if target is not None and self.pointer_position() != target:
                self.move_pointer(target)
            self.press(button)
            self.held = kind == "hold"

    def release_hold(self, *, target=None):
        for index, (queued_at, kind, button, spot) in enumerate(self.pending):
            if kind == "hold":
                # The press never got its turn: deliver it as an ordinary click instead.
                self.pending[index] = (queued_at, "click", button, spot)
                return
        if self.held and self.pressed_button is not None:
            if target is not None and self.pointer_position() != target:
                self.move_pointer(target)
            self.held = False
            self.release()

    def press(self, button):
        # Track the injection attempt so cleanup can release after a partial backend failure.
        self.pressed_button = button
        self.set_button(button, True)
        self.transition_at = monotonic() + CLICK_HOLD
        if self.on_press is not None:
            self.on_press(button)

    def release(self):
        self.set_button(self.pressed_button, False)
        self.pressed_button = None
        self.transition_at = monotonic() + CLICK_GAP

    def advance(self):
        now = monotonic()
        while self.pending and now - self.pending[0][0] > CLICK_QUEUE_LIFETIME:
            self.pending.popleft()
        if now < self.transition_at:
            return
        if self.pressed_button is not None:
            if self.held:
                return
            self.release()
            if self.deferred_pointer is not None:
                pointer, self.deferred_pointer = self.deferred_pointer, None
                self.move_pointer(pointer)
        elif self.pending:
            _, kind, button, target = self.pending.popleft()
            current = self.pointer_position()
            if current != target:
                if kind == "click":
                    self.deferred_pointer = current
                self.move_pointer(target)
            self.press(button)
            self.held = kind == "hold"

    def cancel(self):
        self.pending.clear()
        self.deferred_pointer = None
        self.held = False
        if self.pressed_button is not None:
            self.release()


class MappedDesktop:
    """Resolve hand aim and click delivery within the selected display."""

    def setup_pointer(self, width, height):
        self.layout = self.display_layout() or DisplayLayout(((0, 0, width, height),))
        self.active_monitor = self.layout.primary
        self.tracking_started = False
        self.clicks = ClickDelivery(self.set_button, self.move_pointer, self.pointer_position)

    @property
    def size(self):
        return self.layout.extent

    def refresh_layout(self):
        updated = self.display_layout()
        if updated is None or updated == self.layout:
            return False
        # Queued clicks are normalized against the old display arrangement.
        self.cancel_clicks()
        previous_center = self.monitor_center()
        self.layout = updated
        if not self.tracking_started:
            self.active_monitor = updated.primary
        elif self.active_monitor not in updated.rects:
            # Preserve the spatial selection through a resolution change when possible.
            replacement = updated.monitor_at(*previous_center)
            self.active_monitor = replacement or updated.primary
            if replacement is None:
                self.center_pointer()
        return True

    def monitor_center(self):
        left, top, right, bottom = self.active_monitor
        return (left + right) // 2, (top + bottom) // 2

    def clamp_to_monitor(self, x, y):
        left, top, right, bottom = self.active_monitor
        return clamp(x, left, right - 1), clamp(y, top, bottom - 1)

    def center_pointer(self):
        self.move_pixel(*self.monitor_center())

    def switch_monitor(self, direction):
        target = self.layout.adjacent(self.active_monitor, direction)
        if target is None:
            return False
        self.cancel_clicks()
        self.active_monitor = target
        self.tracking_started = True
        self.center_pointer()
        return True

    def steer(self, camera_pointer):
        """Map absolute camera aim onto the active monitor for snapping and delivery."""
        self.tracking_started = True
        left, top, right, bottom = self.active_monitor
        x = left + round(clamp(camera_pointer[0], 0.0, 1.0) * (right - left - 1))
        y = top + round(clamp(camera_pointer[1], 0.0, 1.0) * (bottom - top - 1))
        return self.layout.to_pointer(x, y)

    def pointer_position(self):
        return self.layout.to_pointer(*self.pixel_position())

    def pointer_pixel(self, pointer):
        return self.clamp_to_monitor(*self.layout.to_pixel(pointer, project=False))

    def at_pointer(self, pointer):
        """Check whether the native cursor remains at the resolved hand target."""
        return tuple(map(round, self.pixel_position())) == self.pointer_pixel(pointer)

    def move_pointer(self, pointer):
        self.move_pixel(*self.pointer_pixel(pointer))

    def move(self, pointer):
        self.clicks.move(pointer)

    def click(self, button="left", *, target=None):
        self.clicks.click(button, target=target)

    def hold(self, button="left", *, target=None):
        self.clicks.hold(button, target=target)

    def release_hold(self, *, target=None):
        self.clicks.release_hold(target=target)

    def cancel_clicks(self):
        self.clicks.cancel()


class PynputDesktop(MappedDesktop):
    def __init__(self, width, height):
        if sys.platform == "darwin":
            from HIServices import AXIsProcessTrusted
            from Quartz import CGPreflightListenEventAccess

            if not (AXIsProcessTrusted() and CGPreflightListenEventAccess()):
                raise RuntimeError(
                    "Allow Accessibility and Input Monitoring for Terminal or Python in System Settings, "
                    "then quit and reopen the launcher."
                )

        from pynput import keyboard, mouse

        self.keyboard = keyboard.Controller()
        if sys.platform == "darwin":
            from Quartz import CGEventCreate, CGEventGetLocation

            # pynput also reads this position when generating button-down/up events.
            class QuartzMouse(mouse.Controller):
                def _position_get(self):
                    location = CGEventGetLocation(CGEventCreate(None))
                    return location.x, location.y

            self.mouse = QuartzMouse()
        else:
            self.mouse = mouse.Controller()
        self.key = keyboard.Key
        self.buttons = {"left": mouse.Button.left, "right": mouse.Button.right}
        self.setup_pointer(width, height)
        self.commands = SimpleQueue()
        self.hotkeys = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+<space>": lambda: self.commands.put("pause"),
            "<ctrl>+<alt>+n": lambda: self.commands.put("pad"),
            "<ctrl>+<alt>+r": lambda: self.commands.put("calibrate"),
        })
        self.hotkeys.start()

    def stop(self):
        try:
            self.cancel_clicks()
        finally:
            self.hotkeys.stop()

    def take_commands(self):
        commands = []
        while True:
            try:
                commands.append(self.commands.get_nowait())
            except Empty:
                break
        if commands:
            self.cancel_clicks()
        else:
            self.clicks.advance()
        return commands

    def display_layout(self):
        if sys.platform == "win32":
            return monitors_windows.display_layout()
        if sys.platform == "darwin":
            return monitors_unix.mac_display_layout()
        return None

    def move_pixel(self, x, y):
        if sys.platform == "win32":
            monitors_windows.set_cursor_position(x, y)
        else:
            self.mouse.position = x, y

    def pixel_position(self):
        if sys.platform == "win32":
            return monitors_windows.cursor_position()
        return self.mouse.position

    def set_button(self, button, down):
        if down:
            self.mouse.press(self.buttons[button])
        else:
            self.mouse.release(self.buttons[button])

    def scroll(self, steps):
        self.mouse.scroll(0, steps)

    def zoom(self, steps):
        if sys.platform == "darwin":
            with self.keyboard.pressed(self.key.cmd):
                self.keyboard.tap("=" if steps > 0 else "-")
        else:
            with self.keyboard.pressed(self.key.ctrl):
                self.scroll(steps)

    def type_key(self, key):
        special = {"Enter": self.key.enter, "Backspace": self.key.backspace}
        self.keyboard.tap(special.get(key, key))

    def press_keys(self, names):
        """Press a shortcut such as ["win", "left"]: modifiers held while the rest are tapped."""
        key = self.key
        named = {"ctrl": key.ctrl, "alt": key.alt, "shift": key.shift, "win": key.cmd, "left": key.left,
                 "right": key.right, "up": key.up, "down": key.down, "tab": key.tab, "enter": key.enter,
                 "esc": key.esc, "space": key.space, "home": key.home, "end": key.end, "pageup": key.page_up,
                 "pagedown": key.page_down, "delete": key.delete, "backspace": key.backspace,
                 "play_pause": key.media_play_pause, "next_track": key.media_next,
                 "previous_track": key.media_previous, "volume_up": key.media_volume_up,
                 "volume_down": key.media_volume_down, "mute": key.media_volume_mute}
        if hasattr(key, "insert"):
            named["insert"] = key.insert
        keys = []
        for name in names:
            if name in named:
                keys.append(named[name])
            elif name.startswith("f") and name[1:].isdigit() and hasattr(key, name):
                keys.append(getattr(key, name))
            elif len(name) == 1:
                keys.append(name)
            else:
                raise ValueError(f"This computer can't send the {name} key.")
        pressed = []
        try:
            for item in keys:
                self.keyboard.press(item)
                pressed.append(item)
        finally:
            for item in reversed(pressed):
                self.keyboard.release(item)


class X11Desktop(MappedDesktop):
    def __init__(self, width, height):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
            raise RuntimeError("Desktop control on Linux requires an X11 session. Choose X11 at sign-in.")

        from Xlib import X, XK, display
        from Xlib.ext import xtest

        self.display = display.Display()
        if not self.display.has_extension("XTEST"):
            self.display.close()
            raise RuntimeError("The X11 session does not provide the XTEST input extension.")
        self.x = X
        self.xk = XK
        self.xtest = xtest
        self.setup_pointer(width, height)
        symbols = {"+": "plus", "-": "minus", ".": "period", "Enter": "Return", "Backspace": "BackSpace"}
        names = (*"0123456789", "Ctrl_L", "Ctrl_R", "Alt_L", "Alt_R", "Shift_L", "space", "n", "r", *symbols)
        symbols.update({"Ctrl_L": "Control_L", "Ctrl_R": "Control_R"})
        self.keys = {}
        for name in names:
            keysym = XK.string_to_keysym(symbols.get(name, name))
            mappings = self.display.keysym_to_keycodes(keysym)
            self.keys[name] = min(mappings, key=lambda item: item[1], default=(0, 0))
        self.held = set()

    def stop(self):
        try:
            self.cancel_clicks()
        finally:
            self.display.close()

    def take_commands(self):
        states = self.display.query_keymap()

        def down(name):
            code = self.keys[name][0]
            return bool(code and states[code // 8] & (1 << (code % 8)))

        active = set()
        if (down("Ctrl_L") or down("Ctrl_R")) and (down("Alt_L") or down("Alt_R")):
            if down("space"):
                active.add("pause")
            if down("n"):
                active.add("pad")
            if down("r"):
                active.add("calibrate")
        new = active - self.held
        self.held = active
        if new:
            self.cancel_clicks()
        else:
            self.clicks.advance()
        return new

    def emit(self, event, detail=0, **position):
        self.xtest.fake_input(self.display, event, detail, **position)

    def display_layout(self):
        return monitors_unix.x11_display_layout(self.display)

    def move_pixel(self, x, y):
        self.emit(self.x.MotionNotify, x=x, y=y)
        self.display.flush()

    def pixel_position(self):
        pointer = self.display.screen().root.query_pointer()
        return pointer.root_x, pointer.root_y

    def set_button(self, button, down):
        self.emit(self.x.ButtonPress if down else self.x.ButtonRelease, 1 if button == "left" else 3)
        self.display.flush()

    def scroll(self, steps):
        button = 4 if steps > 0 else 5
        for _ in range(abs(steps)):
            self.emit(self.x.ButtonPress, button)
            self.emit(self.x.ButtonRelease, button)
        self.display.flush()

    def zoom(self, steps):
        ctrl = self.keys["Ctrl_L"][0]
        self.emit(self.x.KeyPress, ctrl)
        try:
            self.scroll(steps)
        finally:
            self.emit(self.x.KeyRelease, ctrl)
            self.display.flush()

    def press_keys(self, names):
        """Press a shortcut such as ["win", "left"] through XTEST."""
        symbols = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "win": "Super_L", "left": "Left",
                   "right": "Right", "up": "Up", "down": "Down", "tab": "Tab", "enter": "Return", "esc": "Escape",
                   "space": "space", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
                   "delete": "Delete", "backspace": "BackSpace", "insert": "Insert",
                   "play_pause": "XF86AudioPlay", "next_track": "XF86AudioNext", "previous_track": "XF86AudioPrev",
                   "volume_up": "XF86AudioRaiseVolume", "volume_down": "XF86AudioLowerVolume",
                   "mute": "XF86AudioMute", "[": "bracketleft", "]": "bracketright", "-": "minus", "=": "equal",
                   ",": "comma", ".": "period", "/": "slash"}
        codes = []
        for name in names:
            symbol = symbols.get(name, name.upper() if name.startswith("f") and name[1:].isdigit() else name)
            code = self.display.keysym_to_keycode(self.xk.string_to_keysym(symbol))
            if not code:
                raise ValueError(f"This computer can't send the {name} key.")
            codes.append(code)
        pressed = []
        try:
            for code in codes:
                self.emit(self.x.KeyPress, code)
                pressed.append(code)
        finally:
            for code in reversed(pressed):
                self.emit(self.x.KeyRelease, code)
            self.display.flush()

    def type_key(self, key):
        code, level = self.keys[key]
        if not code or level > 1:
            return
        shift = self.keys["Shift_L"][0]
        if level == 1:
            self.emit(self.x.KeyPress, shift)
        try:
            self.emit(self.x.KeyPress, code)
            self.emit(self.x.KeyRelease, code)
        finally:
            if level == 1:
                self.emit(self.x.KeyRelease, shift)
            self.display.flush()


def keep_pad_inactive(window):
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = (wintypes.HWND,)
        user32.GetParent.restype = wintypes.HWND
        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
        style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x00000080)
    elif sys.platform == "darwin":
        window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w, "help", "noActivates")
