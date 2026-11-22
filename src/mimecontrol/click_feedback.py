"""Brief, click-through Windows cursor feedback. Native resources are allocated on first use."""

import ctypes
import sys
from math import hypot

from .monitors_windows import _physical_coordinates


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
        ("planes", ctypes.c_uint16), ("depth", ctypes.c_uint16), ("compression", ctypes.c_uint32),
        ("bytes", ctypes.c_uint32), ("xppm", ctypes.c_int32), ("yppm", ctypes.c_int32),
        ("used", ctypes.c_uint32), ("important", ctypes.c_uint32),
    ]


class _Blend(ctypes.Structure):
    _fields_ = [(key, ctypes.c_ubyte) for key in ("operation", "flags", "opacity", "format")]


def _ring(button, size):
    """Make premultiplied BGRA pixels for a single or double outlined ring."""
    center = (size - 1) / 2
    radii = (center * 0.88,) if button == "left" else (center * 0.88, center * 0.53)
    blue, green, red = (255, 207, 46) if button == "left" else (38, 165, 255)
    half_band = max(1.1, size * 0.033)
    pixels = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            distance = hypot(x - center, y - center)
            edge = min(abs(distance - radius) for radius in radii)
            alpha = min(1.0, max(0.0, half_band + 1.7 - edge))
            if alpha:
                # Dark outlines keep the bright rings legible over a pale background.
                b, g, r = (blue, green, red) if edge <= half_band else (48, 40, 24)
                offset = 4 * (y * size + x)
                pixels[offset:offset + 4] = bytes((round(b * alpha), round(g * alpha), round(r * alpha),
                                                    round(255 * alpha)))
    return bytes(pixels)


def _placement(pixel, monitor, size):
    x, y = pixel
    ml, mt, mr, mb = monitor
    if not (ml <= x < mr and mt <= y < mb):
        return None
    left, top = x - size // 2, y - size // 2
    dest_x, dest_y = max(ml, left), max(mt, top)
    width, height = min(mr, left + size) - dest_x, min(mb, top + size) - dest_y
    return (dest_x, dest_y), (width, height), (dest_x - left, dest_y - top)


def _declare(library, declarations):
    for name, (result, *parameters) in declarations.items():
        function = getattr(library, name)
        function.restype, function.argtypes = result, parameters


class _LayeredWindow:
    def __init__(self, size):
        self.user = ctypes.WinDLL("user32")
        self.gdi = ctypes.WinDLL("gdi32")
        ptr, integer, unsigned = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
        point_ptr = ctypes.POINTER(_Point)
        _declare(self.user, {
            "CreateWindowExW": (ptr, unsigned, ctypes.c_wchar_p, ctypes.c_wchar_p, unsigned, integer, integer,
                                integer, integer, ptr, ptr, ptr, ptr),
            "UpdateLayeredWindow": (integer, ptr, ptr, point_ptr, point_ptr, ptr, point_ptr, unsigned,
                                    ctypes.POINTER(_Blend), unsigned),
            "SetWindowPos": (integer, ptr, ptr, integer, integer, integer, integer, unsigned),
            "ShowWindow": (integer, ptr, integer), "DestroyWindow": (integer, ptr),
        })
        _declare(self.gdi, {
            "CreateCompatibleDC": (ptr, ptr), "DeleteDC": (integer, ptr),
            "CreateDIBSection": (ptr, ptr, ctypes.POINTER(_BitmapInfo), unsigned, ctypes.POINTER(ptr), ptr, unsigned),
            "SelectObject": (ptr, ptr, ptr), "DeleteObject": (integer, ptr),
        })
        self.window = self.dc = self.bitmap = self.previous = None
        self.bits = ptr()
        self.images = {}
        self.button = None
        self.size = size
        try:
            # WS_EX_LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW. The standard
            # STATIC class needs no custom window procedure or keyboard focus.
            styles = 0x00080000 | 0x00000020 | 0x08000000 | 0x00000080
            with _physical_coordinates(self.user):
                self.window = self.user.CreateWindowExW(styles, "STATIC", "", 0x88000000, 0, 0, 1, 1,
                                                        None, None, None, None)
            self.dc = self.gdi.CreateCompatibleDC(None)
            info = _BitmapInfo(size=ctypes.sizeof(_BitmapInfo), width=size, height=-size, planes=1, depth=32)
            self.bitmap = self.gdi.CreateDIBSection(None, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
            if not (self.window and self.dc and self.bitmap and self.bits):
                raise OSError("Could not create click feedback window or bitmap.")
            self.previous = self.gdi.SelectObject(self.dc, self.bitmap)
            if not self.previous:
                raise OSError("Could not select click feedback bitmap.")
        except Exception:
            self.close()
            raise

    def show(self, button, pixel, monitor):
        placement = _placement(pixel, monitor, self.size)
        if placement is None:
            self.hide()
            return
        if button != self.button:
            if button not in self.images:
                self.images[button] = _ring(button, self.size)
            image = self.images[button]
            ctypes.memmove(self.bits, image, len(image))
            self.button = button
        dest, extent, source = (_Point(*coordinates) for coordinates in placement)
        blend = _Blend(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        with _physical_coordinates(self.user):
            if not self.user.UpdateLayeredWindow(self.window, None, ctypes.byref(dest), ctypes.byref(extent),
                                                 self.dc, ctypes.byref(source), 0, ctypes.byref(blend), 2):
                raise OSError("Could not display click feedback.")
            # HWND_TOPMOST, SWP_NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW
            if not self.user.SetWindowPos(self.window, -1, 0, 0, 0, 0, 0x53):
                raise OSError("Could not raise click feedback.")

    def hide(self):
        if self.window:
            self.user.ShowWindow(self.window, 0)

    def close(self):
        if self.window:
            self.user.DestroyWindow(self.window)
        if self.previous:
            self.gdi.SelectObject(self.dc, self.previous)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        if self.dc:
            self.gdi.DeleteDC(self.dc)
        self.window = self.dc = self.bitmap = self.previous = None


class WindowsClickFeedback:
    def __init__(self, root):
        self.root = root
        self.window = None
        self.timer = None
        self.closed = False

    def show(self, button, pixel, monitor):
        if self.closed or sys.platform != "win32" or button not in ("left", "right") or monitor is None:
            return
        try:
            if self.window is None:
                scale = max(1, min(2.5, self.root.winfo_fpixels("1i") / 96))
                self.window = _LayeredWindow(2 * round(19 * scale) + 1)
            if self.timer is not None:
                self.root.after_cancel(self.timer)
            self.window.show(button, pixel, monitor)
            self.timer = self.root.after(190, self.hide)
        except Exception:
            # Visual feedback is optional even when a desktop or window is shutting down.
            self.close()

    def hide(self):
        if self.timer is not None:
            timer, self.timer = self.timer, None
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.hide()
            except Exception:
                self.close()

    def close(self):
        self.closed = True
        window, self.window = self.window, None
        self.hide()
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
