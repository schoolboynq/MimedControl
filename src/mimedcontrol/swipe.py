from dataclasses import dataclass
from math import dist, hypot


REST_TIME = 0.11
STROKE_TIME = 0.26
COOLDOWN = 0.48


@dataclass
class Stroke:
    origin: tuple[float, float]
    time: float
    span: float
    axis: int
    sign: int
    steps: int = 0
    largest_step: float = 0.0


class ScreenSwipe:
    def __init__(self):
        self.last_swipe = None
        self.hold_after_switch = False
        self.tail_active = False
        self.interrupt()

    def interrupt(self):
        self.previous = None
        self.quiet = None
        self.stroke = None

    def confirm(self):
        self.hold_after_switch = self.tail_active

    def settling(self, now):
        if self.tail_active:
            elapsed = now - self.last_swipe
            settled = self.quiet is not None and now - self.quiet[1] >= 0.09
            if elapsed >= COOLDOWN or (elapsed >= 0.15 and settled):
                self.tail_active = False
                self.hold_after_switch = False
        return self.tail_active

    def update(self, point, span, now):
        if point is None:
            self.interrupt()
            return None
        previous = self.previous
        self.previous = point, now
        if previous is None or not 0 < now - previous[1] <= 0.13:
            self.quiet = point, now
            self.stroke = None
            return None

        last, last_time = previous
        dt = now - last_time
        delta = point[0] - last[0], point[1] - last[1]
        speed = hypot(*delta) / dt
        rested = self.quiet is not None and last_time - self.quiet[1] >= REST_TIME
        if speed <= max(0.16, 1.1 * span):
            if self.quiet is None or dist(point, self.quiet[0]) > max(0.007, 0.10 * span):
                self.quiet = point, now
        else:
            self.quiet = None

        if self.last_swipe is not None and now - self.last_swipe < COOLDOWN:
            return None
        if self.stroke is None:
            axis = int(abs(delta[1]) > abs(delta[0]))
            if not rested or abs(delta[axis]) / dt < max(0.50, 4.0 * span):
                return None
            if abs(delta[1 - axis]) > 0.47 * abs(delta[axis]):
                return None
            self.stroke = Stroke(last, last_time, span, axis, 1 if delta[axis] > 0 else -1)

        stroke = self.stroke
        travel = point[0] - stroke.origin[0], point[1] - stroke.origin[1]
        forward = delta[stroke.axis] * stroke.sign
        progress = travel[stroke.axis] * stroke.sign
        elapsed = now - stroke.time
        too_much_depth_change = min(stroke.span, span) < 0.70 * max(stroke.span, span)
        if (elapsed > STROKE_TIME or too_much_depth_change or forward < -max(0.006, 0.06 * span)
                or abs(travel[1 - stroke.axis]) > 0.47 * progress):
            self.stroke = None
            return None
        if forward / dt >= max(0.45, 3.5 * stroke.span):
            stroke.steps += 1
            stroke.largest_step = max(stroke.largest_step, forward)
        if (stroke.steps < 2 or stroke.largest_step > 0.80 * progress
                or progress < max(0.075, 0.95 * stroke.span)
                or progress / elapsed < max(0.65, 4.0 * stroke.span)):
            return None

        self.last_swipe = now
        self.tail_active = True
        self.hold_after_switch = False
        self.stroke = self.quiet = None
        return (stroke.sign, 0) if stroke.axis == 0 else (0, stroke.sign)
