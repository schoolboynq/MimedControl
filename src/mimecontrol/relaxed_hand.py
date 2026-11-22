from math import dist

import numpy as np

RELAXED_HAND = "Relaxed hand"
BASES = (5, 9, 13, 17)


def relaxed_shape(joints, fingers, index_gap, middle_gap, open_palm):
    """Measure bends along the hand's own axes, using palm lengths as units."""
    bases = joints[list(BASES)]
    tips = joints[8:21:4]
    axes = bases - joints[0]
    across = bases[-1] - bases[0]
    lengths = np.linalg.norm(axes, axis=1)
    size = max(lengths[1], np.linalg.norm(across))
    if size <= 1e-6 or lengths.min() < 0.1 * size:
        return "other"
    offsets = tips - bases
    forward = np.sum(offsets * axes, axis=1) / (lengths * size)
    reach = np.linalg.norm(offsets, axis=1) / size
    # A naturally bent finger can still project well beyond its knuckle. A
    # tightly curled fingertip is both folded and inside its own knuckle line.
    out = (np.asarray(fingers) | (forward > 0.47)) & (reach > 0.48)
    tucked = (~np.asarray(fingers)) & (forward < 0.07) & (reach < 0.55)
    if out[0] and tucked[1:].all() and index_gap > 0.44:
        return "right"
    if (
        max(index_gap, middle_gap) <= 0.42
        and min(index_gap, middle_gap) <= 0.33
        and dist(tips[0], tips[1]) <= 0.42 * size
        and not tucked[:2].all()
    ):
        return "left"

    thumb = joints[4]
    thumb_across = (thumb - bases[0]) @ across / max(float(across @ across), 1e-12)
    thumb_folded = thumb_across > 0.12 and dist(thumb, (joints[0] + bases[1]) * 0.5) < 0.85 * size
    if out[0] and out[1] and min(index_gap, middle_gap) > 0.49:
        if thumb_folded and not out[3]:
            return "zoom" if out[2] else "scroll"
        if not thumb_folded:
            if open_palm and min(forward[2:]) > 0.41 and all(fingers):
                return "palm"
            if not tucked[2:].all():
                return "ready"
    return "other"


class RelaxedClicks:
    """Require navigation between presses and hold the last aim through a squeeze."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.armed = self.latched = False
        self.ready_frames = 0
        self.pending = self.departed = self.last_time = None

    @property
    def active(self):
        return self.armed or self.latched

    def latch(self, now):
        """Wait for the aiming pose again, as after a click."""
        self.armed, self.latched = False, True
        self.ready_frames = 0
        self.pending = self.departed = None
        self.last_time = now

    def update(self, shape, now):
        if self.last_time is not None and not 0 < now - self.last_time <= 0.18:
            self.reset()
        self.last_time = now
        if shape == "ready":
            self.ready_frames += 1
            if self.ready_frames >= 2:
                self.armed, self.latched = True, False
            self.pending = self.departed = None
            return self.latched, None

        self.ready_frames = 0
        if self.latched:
            return True, None
        if not self.armed:
            return False, None
        if self.departed is None:
            self.departed = now
        if now - self.departed > 0.9:
            self.reset()
            return False, None
        if shape in ("left", "right") and self.pending == shape:
            self.armed, self.latched = False, True
            return True, shape
        self.pending = shape
        return True, None
