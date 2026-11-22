"""Best-effort discovery of nearby interactive controls using Windows UI Automation.

Only things you can act on count: buttons, links, check boxes, tabs, sliders, editable fields, and list
items you can select or open. Plain text, documents, read-only fields and decorative items are left out,
so the pointer glides over regular text and only catches on what you can click or type into.
"""

import ctypes
import os
import sys
from dataclasses import dataclass
from math import hypot
from threading import Condition, Thread
from time import monotonic
from uuid import UUID


_POLL_INTERVAL = 0.08
_CACHE_TTL = 0.18
_SEARCH_BUDGET = 0.11
_STILL_TARGET_REFRESH = 0.15
_STILL_EMPTY_REFRESH = 0.55

# UI Automation control types. Controls that always do something when clicked:
# button, check box, combo box, hyperlink, menu item, radio button, slider, spinner, tab item, split button.
_ACTIONABLE = {50000, 50002, 50003, 50005, 50011, 50013, 50015, 50016, 50019, 50031}
# Edit, list item, tree item and data item: interactive only when editable, selectable or openable.
# Web pages show ordinary bulleted text as list items, and read-only fields as edits.
_MAYBE = {50004, 50007, 50024, 50029}
_EDIT = 50004
# Text and document: regular text the pointer skims over, even inside a list or table.
_TEXT = {50020, 50030}
# Bounds, control type, enabled, control element, offscreen, keyboard focusable, and whether the element
# can be invoked, toggled, selected, expanded, or holds a value, and whether that value is read-only.
_PROPERTIES = (30001, 30003, 30010, 30016, 30022, 30009, 30031, 30041, 30036, 30028, 30043, 30046)
_INVOKE, _TOGGLE, _SELECT, _EXPAND, _VALUE, _READ_ONLY = 30031, 30041, 30036, 30028, 30043, 30046
_VT_BOOL = 11

_HRESULT = ctypes.c_int32
_INT = ctypes.c_int32
_PTR = ctypes.c_void_p
_OUT_PTR = ctypes.POINTER(_PTR)
_DWORD = ctypes.c_uint32


class _Point(ctypes.Structure):
    _fields_ = [("x", _INT), ("y", _INT)]


class _Rect(ctypes.Structure):
    _fields_ = [(name, _INT) for name in ("left", "top", "right", "bottom")]


class _Variant(ctypes.Structure):
    # VARIANT: a type tag, three reserved words, and a 16-byte value (24 bytes on 64-bit Windows).
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("value", ctypes.c_longlong),
                ("extra", ctypes.c_longlong)]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", _DWORD), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


def _guid(value):
    return _Guid.from_buffer_copy(UUID(value).bytes_le)


def _call(pointer, index, argtypes, *args, result=_HRESULT):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_PTR)))[0]
    method = ctypes.WINFUNCTYPE(result, _PTR, *argtypes)(table[index])
    return method(pointer, *args)


def _release(pointer):
    if pointer:
        _call(pointer, 2, (), result=_DWORD)  # IUnknown.Release


def _check(hr):
    if hr < 0:
        raise OSError(f"UI Automation error 0x{hr & 0xFFFFFFFF:08X}")


def _output(pointer, index, *args, argtypes=()):
    output = _PTR()
    _check(_call(pointer, index, (*argtypes, _OUT_PTR), *args, ctypes.byref(output)))
    return output


def _property(element, index, value_type):
    value = value_type()
    _check(_call(element, index, (ctypes.POINTER(value_type),), ctypes.byref(value)))
    return value


class _Automation:
    def __init__(self):
        self.ole = ctypes.WinDLL("ole32")
        self.ole.CoInitializeEx.argtypes = (_PTR, _DWORD)
        self.ole.CoInitializeEx.restype = _HRESULT
        self.ole.CoUninitialize.restype = None
        self.ole.CoCreateInstance.argtypes = (ctypes.POINTER(_Guid), _PTR, _DWORD, ctypes.POINTER(_Guid), _OUT_PTR)
        self.ole.CoCreateInstance.restype = _HRESULT
        self.automation = _PTR()
        self.walker = self.cache = None
        self.variant_clear = ctypes.WinDLL("oleaut32").VariantClear
        self.variant_clear.argtypes = (ctypes.POINTER(_Variant),)
        self.variant_clear.restype = _HRESULT
        _check(self.ole.CoInitializeEx(None, 0))  # COINIT_MULTITHREADED
        try:
            client = _guid("e22ad333-b25f-460c-83d0-0581107395c9")  # CUIAutomation8
            interface = _guid("34723aff-0c9d-49d0-9896-7ab52df8cd8a")  # IUIAutomation2
            _check(self.ole.CoCreateInstance(ctypes.byref(client), None, 1, ctypes.byref(interface),
                                             ctypes.byref(self.automation)))
            # IUIAutomation2 connection and transaction timeouts in milliseconds.
            _check(_call(self.automation, 61, (_DWORD,), 150))
            _check(_call(self.automation, 63, (_DWORD,), 150))
            self.walker = _output(self.automation, 14)  # get_ControlViewWalker
            self.cache = _output(self.automation, 20)  # CreateCacheRequest
            for property_id in _PROPERTIES:
                _check(_call(self.cache, 3, (_INT,), property_id))  # AddProperty
        except Exception:
            self.close()
            raise

    def close(self):
        for pointer in (self.cache, self.walker, self.automation):
            _release(pointer)
        self.ole.CoUninitialize()

    def flag(self, element, property_id):
        """A cached true/false property. Unsupported properties come back as a special object, not a bool."""
        variant = _Variant()
        _check(_call(element, 12, (_INT, ctypes.POINTER(_Variant)), property_id, ctypes.byref(variant)))
        try:
            return variant.vt == _VT_BOOL and (variant.value & 0xFFFF) != 0
        finally:
            self.variant_clear(ctypes.byref(variant))  # GetCachedPropertyValue

    def interactive(self, element, control):
        """True for something you can act on, False for text or a passive item, None to look at its parent."""
        if control in _TEXT:
            return None  # Text inside a link or button still finds the link or button above it.
        acts = any(self.flag(element, prop) for prop in (_INVOKE, _TOGGLE, _SELECT, _EXPAND))
        focusable = bool(_property(element, 59, _INT).value)  # CachedIsKeyboardFocusable
        if control in _ACTIONABLE:
            return True
        if control == _EDIT:
            return self.flag(element, _VALUE) and not self.flag(element, _READ_ONLY) and focusable
        if control in _MAYBE:
            return acts or focusable
        # Custom, group, image and other containers count only when clickable and reachable by Tab.
        return True if acts and focusable else None

    def rectangle_at(self, x, y, deadline):
        element = None
        try:
            # ElementFromPointBuildCache loads the requested properties in one provider call.
            element = _output(self.automation, 11, _Point(x, y), self.cache, argtypes=(_Point, _PTR))
            for level in range(3):
                if not element or monotonic() >= deadline:
                    break
                control = _property(element, 53, _INT).value  # CachedControlType
                verdict = self.interactive(element, control)
                if verdict is not None:
                    if (not verdict or not _property(element, 60, _INT).value
                            or not _property(element, 65, _INT).value or _property(element, 70, _INT).value):
                        return None
                    rect = _property(element, 75, _Rect)  # CachedBoundingRectangle
                    return rect.left, rect.top, rect.right, rect.bottom
                if level < 2:
                    parent = _output(self.walker, 9, element, self.cache, argtypes=(_PTR, _PTR))
                    _release(element)
                    element = parent
        except OSError:
            return None
        finally:
            _release(element)
        return None


class _Windows:
    def __init__(self):
        self.user = ctypes.WinDLL("user32")
        self.user.GetForegroundWindow.argtypes = ()
        self.user.GetForegroundWindow.restype = _PTR
        self.user.WindowFromPhysicalPoint.argtypes = (_Point,)
        self.user.WindowFromPhysicalPoint.restype = _PTR
        self.user.GetAncestor.argtypes = (_PTR, _DWORD)
        self.user.GetAncestor.restype = _PTR
        self.user.GetWindowThreadProcessId.argtypes = (_PTR, ctypes.POINTER(_DWORD))
        self.user.GetWindowThreadProcessId.restype = _DWORD

    def window_at(self, x, y):
        hwnd = self.user.WindowFromPhysicalPoint(_Point(x, y))
        return hwnd and self.user.GetAncestor(hwnd, 2)  # GA_ROOT

    def context(self, x, y):
        foreground = self.user.GetForegroundWindow()
        surface = self.window_at(x, y)
        if not foreground or not surface:
            return None
        owner = self.user.GetAncestor(foreground, 3)  # GA_ROOTOWNER
        if not owner or self.user.GetAncestor(surface, 3) != owner:
            return None
        pid = _DWORD()
        if not self.user.GetWindowThreadProcessId(surface, ctypes.byref(pid)) or pid.value == os.getpid():
            return None
        return foreground, surface


@dataclass(frozen=True)
class _Query:
    x: int
    y: int
    radius: int
    context: tuple[int, int]


@dataclass(frozen=True)
class TargetSnapshot:
    bounds: tuple[tuple[int, int, int, int], ...]
    context: tuple[int, int] | None
    pending: bool = False


def _probes(query):
    half = max(1, query.radius // 2)
    yield query.x, query.y
    for distance in (half, query.radius):
        for dx, dy in ((distance, 0), (-distance, 0), (0, distance), (0, -distance)):
            yield query.x + dx, query.y + dy
        if distance == half:
            for dx, dy in ((half, half), (-half, half), (half, -half), (-half, -half)):
                yield query.x + dx, query.y + dy


def _contains(rect, x, y):
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def _valid_rect(rect, x, y):
    # Interactive surfaces up to a wide text field or a large button; bigger regions are layout, not targets.
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    return (_contains(rect, x, y)
            and 5 <= width <= 720 and 5 <= height <= 240 and width * height <= 96_000)


def _center(rect):
    left, top, right, bottom = rect
    return (left + right) // 2, (top + bottom) // 2


def _search(windows, automation, query, deadline):
    found = []
    for x, y in _probes(query):
        if monotonic() >= deadline or len(found) >= 6:
            break
        if any(_contains(rect, x, y) for rect in found) or windows.window_at(x, y) != query.context[1]:
            continue
        rect = automation.rectangle_at(x, y, deadline)
        if monotonic() >= deadline:
            break
        if rect is None or not _valid_rect(rect, x, y):
            continue
        if windows.window_at(*_center(rect)) == query.context[1]:
            found.append(rect)
            if (x, y) == (query.x, query.y):
                left, top, right, bottom = rect
                cx, cy = _center(rect)
                interior = min(x - left, y - top, right - 1 - x, bottom - 1 - y)
                if interior >= 3 and hypot(cx - x, cy - y) <= query.radius / 2:
                    break
    return tuple(found)


class WindowsTargets:
    """Nonblocking control lookup. UI Automation runs only on a lazy, latest-request worker."""

    def __init__(self):
        try:
            self._windows = _Windows() if sys.platform == "win32" else None
        except (AttributeError, OSError):
            self._windows = None
        self._condition = Condition()
        self._pending = self._snapshot = self._worker = None
        self._closed = False

    def nearby(self, x: int, y: int, radius: int) -> tuple[tuple[int, int, int, int], ...]:
        return self.lookup(x, y, radius).bounds

    def lookup(self, x: int, y: int, radius: int) -> TargetSnapshot:
        windows = self._windows
        if windows is None or self._closed:
            return TargetSnapshot((), None)
        context = windows.context(x, y)
        with self._condition:
            if self._closed:
                return TargetSnapshot((), None)
            if context is None:
                self._pending = self._snapshot = None
                return TargetSnapshot((), None)
            query = _Query(x, y, max(8, min(radius, 100)), context)
            bounds, fresh = self._cached(query, monotonic())
            if not fresh:
                self._pending = query
                if self._worker is None:
                    self._worker = Thread(target=self._run, name="webcam-windows-targets", daemon=True)
                    self._worker.start()
                self._condition.notify()
        visible = tuple(rect for rect in bounds if windows.window_at(*_center(rect)) == context[1])
        return TargetSnapshot(visible, context, pending=not fresh and not bounds)

    def visible(self, x: int, y: int, landing_x: int, landing_y: int) -> bool:
        """Check that a proposed correction remains on the same visible foreground surface."""
        windows = self._windows
        if windows is None or self._closed:
            return False
        context = windows.context(x, y)
        return context is not None and windows.context(landing_x, landing_y) == context

    def _cached(self, query, now):
        if self._snapshot is None:
            return (), False
        when, last, observed = self._snapshot
        if last.context != query.context or last.radius != query.radius:
            return (), False
        age = now - when
        distance = hypot(query.x - last.x, query.y - last.y)
        still = distance <= max(4, min(8, query.radius / 4))
        if not observed:
            return (), still and age < _STILL_EMPTY_REFRESH
        if distance > query.radius / 2 or age > _CACHE_TTL:
            return (), False
        refresh = _STILL_TARGET_REFRESH if still else _POLL_INTERVAL
        return observed, age < refresh

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = self._snapshot = None
            self._condition.notify()

    def _run(self):
        try:
            automation = _Automation()
        except (AttributeError, OSError):
            self.close()
            return
        windows = self._windows
        try:
            started = 0.0
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None)
                    delay = _POLL_INTERVAL - (monotonic() - started)
                    if delay > 0 and not self._closed:
                        self._condition.wait(delay)
                        continue
                    if self._closed:
                        return
                    query, self._pending = self._pending, None
                    if self._cached(query, monotonic())[1]:
                        continue
                started = monotonic()
                if windows.context(query.x, query.y) == query.context:
                    bounds = _search(windows, automation, query, started + _SEARCH_BUDGET)
                    if monotonic() - started > _CACHE_TTL:
                        bounds = ()
                    if windows.context(query.x, query.y) == query.context:
                        with self._condition:
                            if not self._closed:
                                self._snapshot = started, query, bounds
        finally:
            automation.close()
