"""Monitor bounds in the desktop's pointer coordinates."""

from math import ceil, isfinite

from .displays import DisplayLayout


def _layout(sizes):
    rects = set()
    primary = None
    for x, y, width, height, is_primary in sizes:
        edges = (x, y, x + width, y + height)
        if not all(map(isfinite, edges)):
            continue
        left, top, right, bottom = map(ceil, edges)
        if left < right and top < bottom:
            rect = left, top, right, bottom
            rects.add(rect)
            if is_primary:
                primary = rect
    return DisplayLayout(tuple(rects), primary) if rects else None


def x11_display_layout(display):
    """Read logical monitor geometry from an existing X11 connection."""
    try:
        from Xlib.error import ConnectionClosedError, DisplayError, XError
    except ImportError:
        return None
    errors = (AttributeError, ConnectionClosedError, DisplayError, OSError, XError)
    try:
        root = display.screen().root
    except errors:
        return None

    try:
        # python-xlib exposes get_monitors only when the server supports RandR 1.5.
        monitors = root.xrandr_get_monitors(is_active=True).monitors
        layout = _layout((m.x, m.y, m.width_in_pixels, m.height_in_pixels, getattr(m, "primary", False))
                         for m in monitors)
        if layout:
            return layout
    except errors:
        pass

    try:
        # Older RandR servers can still report the running display controllers.
        try:
            resources = root.xrandr_get_screen_resources_current()
        except (AttributeError, XError):
            resources = root.xrandr_get_screen_resources()
        try:
            primary = root.xrandr_get_output_primary().output
        except errors:
            primary = None
        controllers = (display.xrandr_get_crtc_info(crtc, resources.config_timestamp) for crtc in resources.crtcs)
        layout = _layout((c.x, c.y, c.width, c.height, primary in getattr(c, "outputs", ()))
                         for c in controllers if c.status == 0 and c.mode)
        if layout:
            return layout
    except errors:
        pass

    try:
        if display.xinerama_is_active():
            screens = display.xinerama_query_screens().screens
            layout = _layout((s.x, s.y, s.width, s.height, i == 0) for i, s in enumerate(screens))
            if layout:
                return layout
    except errors:
        pass

    try:
        geometry = root.get_geometry()
        return _layout(((0, 0, geometry.width, geometry.height, True),))
    except errors:
        return None


def x11_display_rects(display):
    layout = x11_display_layout(display)
    return layout.rects if layout is not None else ()


def mac_display_layout():
    """Read Quartz display bounds in the same global units as CGEvent pointer coordinates."""
    try:
        import Quartz

        error, displays, count = Quartz.CGGetActiveDisplayList(64, None, None)
        if error or not count:
            return None
        # Quartz documents the main display as the first active display.
        primary = Quartz.CGMainDisplayID() if hasattr(Quartz, "CGMainDisplayID") else displays[0]
        bounds = ((display, Quartz.CGDisplayBounds(display)) for display in displays[:count])
        return _layout((b.origin.x, b.origin.y, b.size.width, b.size.height, display == primary)
                       for display, b in bounds)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


def mac_display_rects():
    layout = mac_display_layout()
    return layout.rects if layout is not None else ()
