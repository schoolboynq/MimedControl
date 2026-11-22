from collections import deque
from dataclasses import dataclass
from math import dist, log


@dataclass(frozen=True)
class Observation:
    time: float
    depth: float
    span: float
    position: tuple[float, float]


class AirTap:
    """Recognize a quick approach and re-arm when the hand pulls back."""

    def __init__(self):
        self.recent = deque(maxlen=8)
        self.reset()

    def reset(self):
        self.recent.clear()
        self.anchor = self.started = self.clicked = self.last_time = None

    def update(self, depth, span, position, now):
        if depth is None:
            self.reset()
            return False, False
        if self.last_time is not None and not 0 < now - self.last_time <= 0.15:
            self.reset()
        self.last_time = now
        # Log differences measure proportional change independent of hand size or initial distance.
        sample = Observation(now, log(depth), log(span), position)

        if self.anchor is not None:
            approach = self.anchor.depth - sample.depth
            expansion = sample.span - self.anchor.span
            if self.clicked is not None:
                if approach > 0.045 or expansion > 0.055:
                    return now - self.clicked < 0.45, False
            elif approach >= 0.015 and now - self.started <= 0.17 and self.is_forward(sample, self.anchor, approach):
                # Require a second observation so a single bad frame can't click.
                if approach >= 0.115 and expansion >= 0.075 and approach >= 0.53 * (now - self.anchor.time):
                    self.clicked = now
                    return True, True
                return True, False
            self.reset()
            self.last_time = now

        while self.recent and now - self.recent[0].time > 0.16:
            self.recent.popleft()
        if len(self.recent) >= 2:
            anchor = max(self.recent, key=lambda observed: observed.depth)
            approach = anchor.depth - sample.depth
            if (approach >= 0.036 and approach >= 0.38 * (now - anchor.time)
                    and sample.span - anchor.span >= 0.024 and self.is_forward(sample, anchor, approach)):
                self.anchor, self.started = anchor, now
                return True, False
        self.recent.append(sample)
        return False, False

    @staticmethod
    def is_forward(sample, anchor, approach):
        return dist(sample.position, anchor.position) <= 0.03 + 0.22 * approach
