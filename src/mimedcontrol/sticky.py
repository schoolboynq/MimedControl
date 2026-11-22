"""Sticky targets: the pointer slows down over things you can click, so it settles on them.

The pointer never jumps. Moving slowly over a button, link, check box or other control, it travels only part
of the way your hand does, so small corrections and tremors keep it on the control. Fast moves cross
controls at full speed. Off a control, the pointer gradually catches up with your hand again.
"""

from math import hypot, inf

from .displays import clamp


def edge_distance(x, y, rect):
    left, top, right, bottom = rect
    return hypot(max(left - x, 0, x - (right - 1)), max(top - y, 0, y - (bottom - 1)))


def smoothstep(value):
    value = clamp(value, 0.0, 1.0)
    return value * value * (3 - 2 * value)


class StickyPointer:
    """Shape the delivered pointer: friction over controls, and a lag that fades as the hand moves."""

    def __init__(self, dpi):
        scale = max(1.0, dpi / 96)
        self.radius = round(12 * scale)  # how far around the pointer to look for a control
        self.margin = 6 * scale  # friction eases in this close outside a control's edge
        self.narrow = 40 * scale  # along an axis, controls this narrow get full friction...
        self.wide = 240 * scale  # ...and this wide or wider none, so long fields and menus don't drag
        self.still = 30 * scale  # hand speeds in pixels per second: holding still...
        self.fast = 900 * scale  # ...and crossing the screen, where controls don't slow the pointer
        self.stickiest = 0.25  # the share of hand motion the pointer keeps on a control when you're slow
        self.max_lag = 64 * scale  # friction never makes the pointer trail the hand by more than this
        self.catch_up = 80 * scale  # hand travel over which the pointer catches up off a control
        self.speed_lag = 0.08  # seconds of smoothing on hand speed, so a tremor's back-and-forth nets out
        self.reset()

    def reset(self):
        self.out = self.hand = self.time = self.monitor = self.speed = None
        self.placed = None
        self.rect = None
        self.rect_seen = 0.0

    def place(self, pointer):
        """The pointer was put here directly (after a drag): carry on from it without a jump."""
        self.placed = pointer

    def update(self, pointer, layout, now, targets=None, *, monitor=None):
        hx, hy = layout.to_pixel(pointer, project=False)
        if monitor is None:
            monitor = layout.monitor_at(hx, hy)
        if monitor is None or not (monitor[0] <= hx < monitor[2] and monitor[1] <= hy < monitor[3]):
            self.reset()
            return pointer
        left, top, right, bottom = monitor
        if self.placed is not None:
            px, py = layout.to_pixel(self.placed, project=False)
            self.placed = None
            if left <= px < right and top <= py < bottom:
                self.out, self.hand, self.time, self.monitor = (float(px), float(py)), (hx, hy), now, monitor
                return self.deliver(layout, hx, hy, monitor)
        if self.out is None or self.monitor != monitor or now <= self.time:
            self.out, self.hand, self.time, self.monitor = (float(hx), float(hy)), (hx, hy), now, monitor
            self.rect = None
            return pointer

        dx, dy = hx - self.hand[0], hy - self.hand[1]
        elapsed = now - self.time
        self.hand, self.time = (hx, hy), now
        travel = hypot(dx, dy)
        if elapsed > 0.25:
            # After a pause in tracking, treat a big change as a fast move rather than a slow creep.
            speed, self.speed = inf, None
        else:
            instant = travel / elapsed
            if self.speed is None:
                self.speed = instant
            else:
                self.speed += elapsed / (elapsed + self.speed_lag) * (instant - self.speed)
            speed = self.speed
        ox, oy = self.out
        old_lag = hypot(ox - (hx - dx), oy - (hy - dy))
        stick, gain_x, gain_y = 0.0, 1.0, 1.0
        if targets is not None:
            rect, stick = self.control_at(ox, oy, monitor, targets, now)
            if rect is not None:
                friction = (1 - self.stickiest) * (1 - smoothstep((speed - self.still) / (self.fast - self.still)))
                gain_x = 1 - friction * stick * self.axis_share(rect[2] - rect[0])
                gain_y = 1 - friction * stick * self.axis_share(rect[3] - rect[1])
        lag_x = ox + dx * gain_x - hx
        lag_y = oy + dy * gain_y - hy
        if stick < 1 and travel:
            fade = min(1.0, travel / self.catch_up) * (1 - stick)
            lag_x, lag_y = lag_x * (1 - fade), lag_y * (1 - fade)
        # Friction can't grow the lag past the cap; a bigger lag left by a drag only shrinks, as the hand moves.
        lag, cap = hypot(lag_x, lag_y), max(self.max_lag, old_lag)
        if lag > cap:
            lag_x, lag_y = lag_x * cap / lag, lag_y * cap / lag
        self.out = hx + lag_x, hy + lag_y
        return self.deliver(layout, hx, hy, monitor)

    def deliver(self, layout, hx, hy, monitor):
        left, top, right, bottom = monitor
        ox, oy = self.out
        # A hand at the edge of its reach always reaches the screen's edge, for edge menus and window snapping.
        if hx <= left or hx >= right - 1:
            ox = float(hx)
        if hy <= top or hy >= bottom - 1:
            oy = float(hy)
        self.out = clamp(ox, left, right - 1), clamp(oy, top, bottom - 1)
        return layout.to_pointer(round(self.out[0]), round(self.out[1]))

    def axis_share(self, length):
        return 1 - smoothstep((length - self.narrow) / (self.wide - self.narrow))

    def control_at(self, x, y, monitor, targets, now):
        """The control under or just beside the pointer, and how fully the pointer is on it (0 to 1)."""
        snapshot = targets.lookup(round(x), round(y), self.radius)
        if snapshot.context is None:
            self.rect = None
            return None, 0.0
        best = distance = None
        for left, top, right, bottom in snapshot.bounds:
            rect = max(left, monitor[0]), max(top, monitor[1]), min(right, monitor[2]), min(bottom, monitor[3])
            if rect[0] < rect[2] and rect[1] < rect[3]:
                gap = edge_distance(x, y, rect)
                if distance is None or gap < distance:
                    best, distance = rect, gap
        if best is not None:
            self.rect, self.rect_seen = best, now
        elif self.rect is not None and snapshot.pending and now - self.rect_seen <= 0.15:
            # A lookup is still in flight: keep the control the pointer was just on.
            best, distance = self.rect, edge_distance(x, y, self.rect)
        else:
            self.rect = None
            return None, 0.0
        on = 1.0 if distance <= 0 else max(0.0, 1 - distance / self.margin)
        return (best, on) if on > 0 else (None, 0.0)
