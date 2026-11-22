"""Whole-arm commands made with a closed fist: swings, pushes and pulls.

A fist never aims or clicks in any control mode, so these strokes can't be
confused with pointing, the open-palm hold or two-hand scrolling. Distances are
in palm sizes, which don't change with distance from the camera.
"""

from math import hypot, log

import numpy as np

from .calibration import palm_scale

PALM = [0, 5, 9, 13, 17]
BASES = (5, 9, 13, 17)
REST_SECONDS = 0.12
STROKE_SECONDS = 0.6
SWING = 2.2  # Sideways travel, in palm sizes (about 30 cm).
LIFT = 1.6   # Up or down travel.
PUSH = 0.25  # Change in log distance (about a quarter closer).
PULL = 0.22
OPPOSITES = {"swing_left": "swing_right", "swing_right": "swing_left", "swing_up": "swing_down",
             "swing_down": "swing_up", "push": "pull", "pull": "push"}


def is_fist(points, world=None):
    """All four fingers folded inside their knuckles, measured along the hand's own axes."""
    joints = points if world is None else world
    bases = joints[list(BASES)]
    axes = bases - joints[0]
    across = bases[-1] - bases[0]
    lengths = np.linalg.norm(axes, axis=1)
    size = max(lengths[1], float(np.linalg.norm(across)))
    if size <= 1e-6 or lengths.min() < 0.1 * size:
        return False
    offsets = joints[8:21:4] - bases
    forward = np.sum(offsets * axes, axis=1) / (lengths * size)
    reach = np.linalg.norm(offsets, axis=1) / size
    # A loose C or a relaxed bend still reaches past its knuckle; a fist doesn't.
    return bool(np.all(forward < 0.12) and np.all(reach < 0.6))


class FistGestures:
    def __init__(self):
        self.reset()
        self.last_fired = None

    def reset(self):
        self.previous = None
        self.still_since = None
        self.rest = None
        self.missing_since = None

    def update(self, points, world, width, now, depth_m=None):
        """Feed one frame of the tracked hand. Returns a gesture name when a stroke completes."""
        fist = points is not None and is_fist(points, world)
        if not fist:
            # Fast motion can blur a frame or two; a longer break ends the stroke.
            if self.missing_since is None:
                self.missing_since = now
            if now - self.missing_since > 0.2:
                self.reset()
            return None
        self.missing_since = None
        scale = palm_scale(points, width)
        if scale <= 0:
            return None
        center = points[PALM, :2].mean(axis=0) / width
        # Closer to the camera is positive. Stereo depth beats apparent size when present.
        depth = -log(depth_m) if depth_m else log(scale)
        current = (now, float(center[0]), float(center[1]), depth, scale)
        previous, self.previous = self.previous, current
        if previous is None or not 0 < now - previous[0] <= 0.2:
            self.still_since, self.rest = now, None
            return None
        dt = now - previous[0]
        speed = hypot(current[1] - previous[1], current[2] - previous[2]) / scale / dt
        if speed < 1.0 and abs(current[3] - previous[3]) / dt < 0.6:
            if self.still_since is None:
                self.still_since = now
            if now - self.still_since >= REST_SECONDS:
                self.rest = current
            return None
        self.still_since = None
        rest = self.rest
        if rest is None:
            return None
        if now - rest[0] > STROKE_SECONDS:
            self.rest = None  # Too slow for a stroke: wait for the hand to settle again.
            return None
        dx = (current[1] - rest[1]) / rest[4]
        dy = (current[2] - rest[2]) / rest[4]
        dz = current[3] - rest[3]
        gesture = None
        if abs(dx) >= SWING and abs(dy) <= 0.55 * abs(dx) and abs(dz) <= 0.3:
            gesture = "swing_right" if dx > 0 else "swing_left"  # The preview is mirrored, like a mirror.
        elif abs(dy) >= LIFT and abs(dx) <= 0.6 * abs(dy) and abs(dz) <= 0.3:
            gesture = "swing_down" if dy > 0 else "swing_up"
        elif dz >= PUSH and hypot(dx, dy) <= 1.2:
            gesture = "push"
        elif dz <= -PULL and hypot(dx, dy) <= 1.2:
            gesture = "pull"
        if gesture is None:
            return None
        # Bringing the arm back shouldn't count as the opposite command unless you pause first.
        if self.last_fired is not None:
            name, fired_at = self.last_fired
            if gesture == OPPOSITES[name] and rest[0] - fired_at < 0.45 and now - fired_at < 1.2:
                self.rest = None
                return None
        self.last_fired = gesture, now
        self.rest = None
        return gesture
