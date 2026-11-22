"""Synthetic hand landmarks for tests: 21 points in the MediaPipe layout, in image pixels."""
import numpy as np


def canonical_hand():
    """A right hand, palm toward the camera, fingers straight, in hand units (metres)."""
    p = np.zeros((21, 3))
    p[1:5, :2] = [(0.020, 0.025), (0.040, 0.045), (0.060, 0.060), (0.078, 0.072)]
    knuckles = {5: (0.030, 0.088), 9: (0.008, 0.093), 13: (-0.013, 0.088), 17: (-0.032, 0.078)}
    angles = {5: 0.25, 9: 0.05, 13: -0.15, 17: -0.35}
    bones = {5: (0.040, 0.025, 0.022), 9: (0.045, 0.028, 0.024), 13: (0.042, 0.026, 0.023), 17: (0.033, 0.020, 0.019)}
    for base, xy in knuckles.items():
        p[base, :2] = xy
        direction = np.array([np.sin(angles[base]), np.cos(angles[base])])
        for i, length in enumerate(bones[base]):
            p[base + i + 1, :2] = p[base + i, :2] + length * direction
    return p


def shape(curl):
    """curl per finger, index to pinky: 0 straight, 1 folded into the palm."""
    h = canonical_hand().copy()
    for finger, base in enumerate((5, 9, 13, 17)):
        c = curl[finger]
        lengths = np.linalg.norm(np.diff(np.vstack((h[base], h[base + 1:base + 4])), axis=0), axis=1)
        up = h[base, :2] - h[0, :2]
        up /= np.linalg.norm(up)
        position, angle = h[base].copy(), 0.0
        for joint in range(3):
            angle += c * np.radians(100) if joint else c * np.radians(80)
            position = position + np.r_[up * np.cos(angle), -np.sin(angle)] * lengths[joint]
            h[base + 1 + joint] = position
    return h


def pts(h, center=(320, 240), size=400):
    """Image-like coordinates, y down."""
    p = h * (size, -size, size)
    p[:, :2] += center
    return p


def ready():
    """Relaxed hand aiming pose: thumb, index and middle out, ring and pinky loose."""
    return shape((0.0, 0.0, 0.55, 0.6))


def _meet(h, meet, fingers, depth):
    for i, joint in enumerate(range(1, 5)):  # the thumb swings forward to meet the fingers
        target = h[joint] + (meet - h[4]) * (i + 1) / 4
        h[joint] = h[joint] + (target - h[joint]) * depth
    for base, offset in fingers:
        goal_tip = meet + np.array(offset)
        knuckle = h[base]
        middle = knuckle + (goal_tip - knuckle) * 0.55 + np.array((0, 0.012, -0.012))
        for joint, goal in ((base + 1, knuckle + (middle - knuckle) * 0.6), (base + 2, middle), (base + 3, goal_tip)):
            h[joint] = h[joint] + (goal - h[joint]) * depth
    return h


def squeeze(depth=1.0):
    """Relaxed hand click: index and middle meet the thumb. depth 0 is aiming, 1 is touching."""
    return _meet(ready().copy(), np.array((0.050, 0.120, -0.035)),
                 ((5, (0.006, 0.004, 0.0)), (9, (-0.006, 0.006, 0.0))), depth)


def palm_ready():
    """Palm + pinch aiming pose: a loose hand."""
    return shape((0.12, 0.35, 0.45, 0.5))


def palm_pinch(depth=1.0):
    """Thumb and index meet in front of the palm."""
    return _meet(palm_ready().copy(), np.array((0.055, 0.115, -0.035)), ((5, (0.005, 0.004, 0.0)),), depth)
