"""Discover attached Windows displays in virtual-desktop pixel coordinates."""

import ctypes
import sys
from contextlib import contextmanager
from functools import cache

from .displays import DisplayLayout


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("monitor", _Rect), ("work", _Rect), ("flags", ctypes.c_uint32)]


_MonitorCallback = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Rect), ctypes.c_ssize_t
)
_PER_MONITOR_V2 = -4
_PER_MONITOR = -3


@cache
def _cursor_api():
    user32 = ctypes.WinDLL("user32")
    user32.GetPhysicalCursorPos.argtypes = (ctypes.POINTER(_Point),)
    user32.GetPhysicalCursorPos.restype = ctypes.c_int
    user32.SetPhysicalCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user32.SetPhysicalCursorPos.restype = ctypes.c_int
    return user32


def cursor_position():
    point = _Point()
    if not _cursor_api().GetPhysicalCursorPos(ctypes.byref(point)):
        raise OSError("Windows could not read the physical cursor position.")
    return point.x, point.y


def set_cursor_position(x, y):
    if not _cursor_api().SetPhysicalCursorPos(x, y):
        raise OSError("Windows could not move the physical cursor.")


def configure_dpi():
    """Choose per-monitor coordinates before creating Tk windows or loading pynput."""
    if sys.platform != "win32":
        return
    user32 = ctypes.WinDLL("user32")
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = (ctypes.c_void_p,)
        set_context.restype = ctypes.c_int
        if set_context(_PER_MONITOR_V2):
            return
    except (AttributeError, OSError):
        pass
    try:
        set_awareness = ctypes.WinDLL("shcore").SetProcessDpiAwareness
        set_awareness.argtypes = (ctypes.c_int,)
        set_awareness.restype = ctypes.c_int32
        if set_awareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except (AttributeError, OSError):
        pass
    # A manifest or the host may have already set the process awareness.
    # In that case Windows keeps the existing setting.
    user32.SetProcessDPIAware.argtypes = ()
    user32.SetProcessDPIAware.restype = ctypes.c_int
    user32.SetProcessDPIAware()


@contextmanager
def _physical_coordinates(user32):
    # Enumeration can also be called on a worker with its own DPI context.
    previous = None
    try:
        set_context = user32.SetThreadDpiAwarenessContext
        set_context.argtypes = (ctypes.c_void_p,)
        set_context.restype = ctypes.c_void_p
        previous = set_context(_PER_MONITOR_V2) or set_context(_PER_MONITOR)
    except (AttributeError, OSError):
        pass
    try:
        yield
    finally:
        if previous:
            set_context(previous)


def _primary_rect(user32):
    try:
        monitor_from_window = user32.MonitorFromWindow
        monitor_from_window.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        monitor_from_window.restype = ctypes.c_void_p
        get_info = user32.GetMonitorInfoW
        get_info.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MonitorInfo))
        get_info.restype = ctypes.c_int
        monitor = monitor_from_window(None, 1)  # MONITOR_DEFAULTTOPRIMARY
        info = _MonitorInfo(size=ctypes.sizeof(_MonitorInfo))
        if monitor and get_info(monitor, ctypes.byref(info)) and info.flags & 1:
            rect = info.monitor
            if rect.left < rect.right and rect.top < rect.bottom:
                return rect.left, rect.top, rect.right, rect.bottom
    except (AttributeError, OSError):
        pass
    return None


def display_layout():
    """Return active displays and the native primary in the physical cursor coordinate system."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32")
        with _physical_coordinates(user32):
            primary = _primary_rect(user32)
            boxes = set()

            @_MonitorCallback
            def visit(_monitor, _dc, bounds, _data):
                rect = bounds.contents
                if rect.right > rect.left and rect.bottom > rect.top:
                    boxes.add((rect.left, rect.top, rect.right, rect.bottom))
                return 1

            try:
                enumerate_monitors = user32.EnumDisplayMonitors
                enumerate_monitors.argtypes = (ctypes.c_void_p, ctypes.POINTER(_Rect), _MonitorCallback,
                                               ctypes.c_ssize_t)
                enumerate_monitors.restype = ctypes.c_int
                if enumerate_monitors(None, None, visit, 0) and boxes:
                    return DisplayLayout(tuple(boxes), primary)
            except (AttributeError, OSError):
                pass
            if primary is not None:
                return DisplayLayout((primary,), primary)
            metric = user32.GetSystemMetrics
            metric.argtypes = (ctypes.c_int,)
            metric.restype = ctypes.c_int
            x, y, width, height = (metric(i) for i in (76, 77, 78, 79))  # SM_*VIRTUALSCREEN
            if width > 0 and height > 0:
                return DisplayLayout(((x, y, x + width, y + height),))
    except (AttributeError, OSError):
        pass
    return None


def display_rects():
    layout = display_layout()
    return layout.rects if layout is not None else ()
