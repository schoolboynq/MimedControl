from dataclasses import dataclass, field


Rect = tuple[int, int, int, int]
Pixel = tuple[int, int]
Pointer = tuple[float, float]


def clamp(value, low, high):
    return min(max(value, low), high)


@dataclass(frozen=True)
class DisplayLayout:
    """Map normalized aim onto the union of signed, half-open display rectangles."""

    rects: tuple[Rect, ...]
    primary: Rect | None = None
    bounds: Rect = field(init=False, compare=False)
    extent: Pixel = field(init=False, compare=False)

    def __post_init__(self):
        rects = tuple(sorted({tuple(rect) for rect in self.rects}))
        if not rects or any(right <= left or bottom <= top for left, top, right, bottom in rects):
            raise ValueError("Display layout requires at least one nonempty monitor rectangle.")
        left = min(rect[0] for rect in rects)
        top = min(rect[1] for rect in rects)
        right = max(rect[2] for rect in rects)
        bottom = max(rect[3] for rect in rects)
        object.__setattr__(self, "rects", rects)
        object.__setattr__(self, "bounds", (left, top, right, bottom))
        if self.primary not in rects:
            # Windows and Quartz put the primary at the origin. This is also a
            # deterministic fallback when an older display server exposes no preference.
            origin = self.nearest_pixel(0, 0)
            object.__setattr__(self, "primary", self.monitor_at(*origin))
        # Spans are inclusive so a normalized coordinate of 1 lands on the last pixel.
        object.__setattr__(self, "extent", (right - left - 1, bottom - top - 1))

    def monitor_at(self, x: int, y: int) -> Rect | None:
        for rect in self.rects:
            left, top, right, bottom = rect
            if left <= x < right and top <= y < bottom:
                return rect
        return None

    def adjacent(self, current: Rect, direction: str) -> Rect | None:
        """Return the closest display in a cardinal direction, preferring shared edges."""
        if direction not in ("left", "right", "up", "down"):
            raise ValueError(f"Unknown display direction {direction!r}")
        if current not in self.rects:
            return None

        def intervals(rect):
            left, top, right, bottom = rect
            if direction in ("up", "down"):
                left, top, right, bottom = top, left, bottom, right
            if direction in ("left", "up"):
                left, right = -right, -left
            return left, right, top, bottom

        near, far, start, end = intervals(current)
        choices = []
        for rect in self.rects:
            ahead, finish, low, high = intervals(rect)
            gap = ahead - far
            if gap < 0:
                continue
            overlap = min(end, high) - max(start, low)
            # Twice the distance between centers, avoiding rounding at shared edges.
            offset = abs((start + end) - (low + high))
            if overlap <= 0 and offset > (ahead + finish) - (near + far):
                continue  # This diagonal monitor lies more in the other direction.
            distance = gap ** 2 + min(0, overlap) ** 2
            choices.append((overlap <= 0, distance, offset, -overlap, rect))
        return min(choices)[-1] if choices else None

    def nearest_pixel(self, x: int, y: int) -> Pixel:
        if self.monitor_at(x, y) is not None:
            return x, y
        candidates = (
            (clamp(x, left, right - 1), clamp(y, top, bottom - 1))
            for left, top, right, bottom in self.rects
        )
        return min(candidates, key=lambda point: (point[0] - x) ** 2 + (point[1] - y) ** 2)

    def to_pixel(self, pointer: Pointer, *, project: bool = True) -> Pixel:
        left, top, _, _ = self.bounds
        span_x, span_y = self.extent
        x = left + round(clamp(pointer[0], 0.0, 1.0) * span_x)
        y = top + round(clamp(pointer[1], 0.0, 1.0) * span_y)
        return self.nearest_pixel(x, y) if project else (x, y)

    def to_pointer(self, x: int, y: int) -> Pointer:
        x, y = self.nearest_pixel(x, y)
        left, top, _, _ = self.bounds
        span_x, span_y = self.extent
        return (x - left) / max(span_x, 1), (y - top) / max(span_y, 1)
