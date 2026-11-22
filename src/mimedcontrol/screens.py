"""Move control to another screen by reaching past the side of your box."""

MARGIN = 0.08   # How far past the box edge counts, as a fraction of the box.
DWELL = 0.35    # How long to keep reaching before switching.


class EdgeSwitch:
    def __init__(self):
        self.blocked = set()
        self.reset()

    def reset(self):
        self.direction = None
        self.since = None
        self.smoothed = None

    def update(self, raw_pointer, now, available):
        """Feed the unclamped pointer. Returns (switch direction or None, pending direction, progress)."""
        if raw_pointer is None:
            self.reset()
            return None, None, 0.0
        x, y = raw_pointer
        if self.smoothed is not None:
            x, y = (s + 0.5 * (n - s) for s, n in zip(self.smoothed, (x, y)))
        self.smoothed = x, y
        # A direction re-arms once the hand comes back inside the box on that axis.
        if 0 <= x <= 1:
            self.blocked -= {"left", "right"}
        if 0 <= y <= 1:
            self.blocked -= {"up", "down"}
        overshoot = {"left": -x - MARGIN, "right": x - 1 - MARGIN, "up": -y - MARGIN, "down": y - 1 - MARGIN}
        direction = max(overshoot, key=overshoot.get)
        if overshoot[direction] <= 0 or direction not in available or direction in self.blocked:
            self.direction = self.since = None
            return None, None, 0.0
        if direction != self.direction:
            self.direction, self.since = direction, now
        progress = min(1.0, (now - self.since) / DWELL)
        if progress < 1:
            return None, direction, progress
        # After switching, the hand is still past this edge, so wait for it to come back first.
        self.blocked.add(direction)
        self.direction = self.since = None
        return direction, None, 1.0
