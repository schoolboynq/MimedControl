"""Gestures you can assign, the actions they can run, and the keys each action sends."""

import sys

GESTURES = (
    ("swing_left", "Fist swing left"),
    ("swing_right", "Fist swing right"),
    ("swing_up", "Fist swing up"),
    ("swing_down", "Fist swing down"),
    ("push", "Fist push toward the screen"),
    ("pull", "Fist pull back toward you"),
    ("palm_hold", "Spread palm, held still"),
    ("edge_push", "Reach past the side of your box"),
    ("flick", "Quick flick toward another screen"),
)
GESTURE_NAMES = dict(GESTURES)
FIST_GESTURES = ("swing_left", "swing_right", "swing_up", "swing_down", "push", "pull")
# These gestures point somewhere, so "Switch screen" knows which way to go.
DIRECTIONS = {"swing_left": "left", "swing_right": "right", "swing_up": "up", "swing_down": "down"}
SCREEN_GESTURES = ("edge_push", "flick")

ACTIONS = {
    "nothing": "Nothing",
    "switch_screen": "Switch screen",
    "number_pad": "Toggle number pad",
    "pause": "Pause tracking",
    "snap_left": "Snap window left",
    "snap_right": "Snap window right",
    "maximize": "Maximize window",
    "minimize": "Minimize window",
    "move_screen_left": "Move window to the left screen",
    "move_screen_right": "Move window to the right screen",
    "show_desktop": "Show desktop",
    "task_view": "Task view",
    "switch_app": "Switch to last app",
    "close_window": "Close window",
    "back": "Back",
    "forward": "Forward",
    "previous_desktop": "Previous desktop",
    "next_desktop": "Next desktop",
    "play_pause": "Play or pause media",
    "next_track": "Next track",
    "previous_track": "Previous track",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "mute": "Mute",
    "custom": "Custom shortcut…",
}

# Keys per platform. A missing platform means the action isn't offered there.
KEYS = {
    "snap_left": {"win32": "win+left", "linux": "win+left"},
    "snap_right": {"win32": "win+right", "linux": "win+right"},
    "maximize": {"win32": "win+up", "darwin": "ctrl+win+f", "linux": "win+up"},
    "minimize": {"win32": "win+down", "darwin": "win+m", "linux": "win+h"},
    "move_screen_left": {"win32": "win+shift+left", "linux": "win+shift+left"},
    "move_screen_right": {"win32": "win+shift+right", "linux": "win+shift+right"},
    "show_desktop": {"win32": "win+d", "darwin": "f11", "linux": "win+d"},
    "task_view": {"win32": "win+tab", "darwin": "ctrl+up", "linux": "win"},
    "switch_app": {"win32": "alt+tab", "darwin": "win+tab", "linux": "alt+tab"},
    "close_window": {"win32": "alt+f4", "darwin": "win+w", "linux": "alt+f4"},
    "back": {"win32": "alt+left", "darwin": "win+[", "linux": "alt+left"},
    "forward": {"win32": "alt+right", "darwin": "win+]", "linux": "alt+right"},
    "previous_desktop": {"win32": "ctrl+win+left", "darwin": "ctrl+left", "linux": "ctrl+alt+left"},
    "next_desktop": {"win32": "ctrl+win+right", "darwin": "ctrl+right", "linux": "ctrl+alt+right"},
    "play_pause": {"win32": "play_pause", "darwin": "play_pause", "linux": "play_pause"},
    "next_track": {"win32": "next_track", "darwin": "next_track", "linux": "next_track"},
    "previous_track": {"win32": "previous_track", "darwin": "previous_track", "linux": "previous_track"},
    "volume_up": {"win32": "volume_up", "darwin": "volume_up", "linux": "volume_up"},
    "volume_down": {"win32": "volume_down", "darwin": "volume_down", "linux": "volume_down"},
    "mute": {"win32": "mute", "darwin": "mute", "linux": "mute"},
}
if sys.platform == "darwin":
    ACTIONS["task_view"] = "Mission Control"

DEFAULTS = {
    "swing_left": "snap_left" if sys.platform != "darwin" else "previous_desktop",
    "swing_right": "snap_right" if sys.platform != "darwin" else "next_desktop",
    "swing_up": "maximize",
    "swing_down": "minimize",
    "push": "task_view",
    "pull": "show_desktop",
    "palm_hold": "number_pad",
    "edge_push": "switch_screen",
    "flick": "switch_screen",
}

MODIFIERS = ("ctrl", "alt", "shift", "win")
ALIASES = {"control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt", "cmd": "win", "command": "win",
           "super": "win", "meta": "win", "windows": "win", "return": "enter", "escape": "esc",
           "pgup": "pageup", "pgdn": "pagedown", "del": "delete", "ins": "insert", "arrowleft": "left",
           "arrowright": "right", "arrowup": "up", "arrowdown": "down", "playpause": "play_pause",
           "plus": "=", "minus": "-", "comma": ",", "period": ".", "slash": "/"}
NAMED = {"left", "right", "up", "down", "tab", "enter", "esc", "space", "home", "end", "pageup", "pagedown",
         "delete", "backspace", "insert", "play_pause", "next_track", "previous_track", "volume_up",
         "volume_down", "mute", *(f"f{number}" for number in range(1, 21))}


def platform_key():
    return "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"


def parse_shortcut(text):
    """Turn "Ctrl+Shift+T" into ["ctrl", "shift", "t"], or raise ValueError with a reason."""
    names = []
    for part in text.replace(" ", "").lower().split("+"):
        if not part:
            raise ValueError("Write keys joined with +, like Ctrl+Shift+T.")
        name = ALIASES.get(part, part)
        if name not in MODIFIERS and name not in NAMED and not (len(name) == 1 and name.isprintable()):
            raise ValueError(f"“{part}” isn't a key name this app knows.")
        if name in names:
            raise ValueError(f"“{part}” appears twice.")
        names.append(name)
    if not names:
        raise ValueError("Type a shortcut, like Ctrl+Shift+T.")
    # Modifiers go first so they're held while the other keys are pressed.
    return sorted(names, key=lambda name: name not in MODIFIERS)


def format_shortcut(names):
    return "+".join(name.capitalize() if len(name) > 1 else name.upper() for name in names)


def available_actions(gesture):
    """Actions that make sense for a gesture on this computer, in menu order."""
    if gesture in SCREEN_GESTURES:
        return ["switch_screen", "nothing"]
    platform = platform_key()
    choices = []
    for action in ACTIONS:
        if action == "switch_screen" and gesture not in DIRECTIONS:
            continue
        if action in KEYS and platform not in KEYS[action]:
            continue
        choices.append(action)
    return choices


def keys_for(action, custom=None):
    """The keys an action presses here, or None when it isn't a key action."""
    if action == "custom":
        return parse_shortcut(custom) if custom else None
    combo = KEYS.get(action, {}).get(platform_key())
    return None if combo is None else parse_shortcut(combo)


def clean_choices(actions, shortcuts):
    """Saved choices, falling back to the defaults for anything unknown or unavailable here."""
    actions = actions if isinstance(actions, dict) else {}
    shortcuts = shortcuts if isinstance(shortcuts, dict) else {}
    chosen, keys = {}, {}
    for gesture in GESTURE_NAMES:
        action = actions.get(gesture)
        if action not in available_actions(gesture):
            action = DEFAULTS[gesture]
        if action == "custom":
            try:
                keys[gesture] = format_shortcut(parse_shortcut(str(shortcuts.get(gesture, ""))))
            except ValueError:
                action = DEFAULTS[gesture]
        chosen[gesture] = action
    return chosen, keys


def describe(action, shortcut=None):
    if action == "custom" and shortcut:
        return shortcut
    return ACTIONS[action]
