from math import dist, hypot, pi


RESPONSES = {
    "Steady": (1.5, 7.0),
    "Balanced": (2.4, 10.0),
    "Fast": (4.5, 13.0),
}
QUIET_RADIUS = {"Steady": .016, "Balanced": .012, "Fast": .008}


def weight(hz, dt):
    return dt / (dt + 1 / (2 * pi * hz))


class PointerFilter:
    """Speed-adaptive One Euro filter with a check for single-frame jumps."""

    def __init__(self, *, elastic=False):
        self.elastic = elastic
        self.reset()

    def reset(self):
        self.value = self.output = None
        self.hold()

    def hold(self):
        self.raw = self.time = self.pending = None
        self.velocity = (0.0, 0.0)
        self.resume_until = 0.0
        self.in_motion = False
        self.quick_until = 0.0

    def update(self, x, y, now, response, *, stabilize=False):
        target = x, y
        if self.value is None:
            self.value = self.output = self.raw = target
            self.time = now
            return self.output

        if self.time is None or now - self.time > 0.14:
            self.hold()
            self.raw, self.time = target, now - 1 / 30
            self.resume_until = now + 0.14
        dt = min(0.06, max(0.005, now - self.time))
        dx, dy = x - self.raw[0], y - self.raw[1]
        jump = hypot(dx, dy)
        if jump > max(0.13, 3.6 * dt):
            previous = self.pending
            self.pending = target
            if previous is None:
                return self.output
            px, py = previous[0] - self.raw[0], previous[1] - self.raw[1]
            if dx * px + dy * py < 0.65 * jump * hypot(px, py):
                return self.output
        self.pending = None
        self.raw, self.time = target, now

        vx, vy = self.velocity
        speed_weight = weight(4, dt)
        self.velocity = vx + speed_weight * (dx / dt - vx), vy + speed_weight * (dy / dt - vy)
        cutoff, speed_gain = RESPONSES[response]
        speed = hypot(*self.velocity)
        if self.elastic:
            cutoff = self.elastic_cutoff(cutoff, speed_gain, speed, jump, dt, now)
        else:
            cutoff += speed_gain * speed
        if stabilize:
            # Filter by displacement from the settled aim. Alternating landmark
            # noise can have high instantaneous speed even on a stationary hand.
            fraction = dist(target, self.value) / QUIET_RADIUS[response]
            if fraction < 1 and jump > .0003:
                quiet = RESPONSES[response][0] * .23
                cutoff = quiet + (cutoff - quiet) * fraction ** 3
        alpha = weight(cutoff, dt)
        old_x, old_y = self.value
        step_x, step_y = alpha * (x - old_x), alpha * (y - old_y)
        if now < self.resume_until:
            limit = 2.0 * dt
            factor = min(1.0, limit / max(hypot(step_x, step_y), 1e-9))
            step_x, step_y = step_x * factor, step_y * factor
        next_x, next_y = old_x + step_x, old_y + step_y
        # Reach exact screen edges even after the final subpixel correction.
        self.value = (
            x if x in (0, 1) and abs(x - next_x) < 0.001 else next_x,
            y if y in (0, 1) and abs(y - next_y) < 0.001 else next_y,
        )
        if dist(self.value, self.output) >= 0.00045 or any(
            coord in (0, 1) and coord != sent for coord, sent in zip(self.value, self.output)
        ):
            self.output = self.value
        return self.output

    def elastic_cutoff(self, cutoff, speed_gain, speed, jump, dt, now):
        if self.in_motion:
            if jump <= max(0.0025, 0.075 * dt) or (speed < 0.08 and jump < 0.006):
                self.in_motion = False
                self.quick_until = now + 0.075
        elif jump >= max(0.009, 0.18 * dt):
            self.in_motion = True
            self.quick_until = now + 0.048

        if now < self.quick_until:
            return cutoff * 6
        # Keep some lag through a sustained movement, while quiet tracking stays smooth.
        return cutoff + min(speed_gain * speed, cutoff * 1.5)
