"""Estimate relative camera delay from the same motion observed in both views."""

import numpy as np

MAX_OFFSET = 0.38


def estimate_motion_offset(first, second):
    """Align free hand motion even when the camera views are rotated or scaled."""
    a, b = (np.asarray(view, dtype=float) for view in (first, second))
    start = max(a[0, 0], b[0, 0]) + MAX_OFFSET + 0.08
    end = min(a[-1, 0], b[-1, 0]) - MAX_OFFSET - 0.08
    grid = np.arange(start, end, 0.025)
    if len(grid) < 200:
        raise ValueError("Keep one hand visible in both previews for longer.")

    def motion(view, stamps):
        times = view[:, 0]
        before, after = stamps - 0.07, stamps + 0.07
        indices = np.clip(np.searchsorted(times, (before, after)), 1, len(times) - 1)
        valid = np.all(times[indices] - times[indices - 1] < 0.11, axis=0)
        tracks = np.array(
            [np.interp(after, times, channel) - np.interp(before, times, channel) for channel in view[:, 1:].T]
        ).T
        return tracks, valid

    def agreement(x, y):
        x, y = x - x.mean(axis=0), y - y.mean(axis=0)
        # Allow relative orientation and perspective scale to change the direction of motion.
        # Penalize each view's unexplained motion, symmetrically.
        scores = []
        for source, target in ((x, y), (y, x)):
            energy = max(np.sum(target**2), 1e-9)
            gram = source.T @ source
            fit = np.linalg.solve(
                gram + (0.025 * np.trace(gram) + 1e-8) * np.eye(source.shape[1]), source.T @ target
            )
            scores.append(1 - np.sum((source @ fit - target) ** 2) / energy)
        return np.mean(scores)

    first_motion, valid_first = motion(a, grid)
    if np.linalg.norm(first_motion[valid_first]) < 0.20:
        raise ValueError("Move your hand around and change direction a few times.")
    offsets = np.arange(-38, 39) * 0.01
    scores = []
    split = len(grid) // 2
    for offset in offsets:
        second_motion, valid_second = motion(b, grid + offset)
        valid = valid_first & valid_second
        early, late = valid[:split], valid[split:]
        if min(early.sum(), late.sum()) < 80:
            scores.append((-1, -1))
            continue
        scores.append(
            (
                agreement(first_motion[:split][early], second_motion[:split][early]),
                agreement(first_motion[split:][late], second_motion[split:][late]),
            )
        )
    scores = np.array(scores)
    pooled = scores.mean(axis=1)
    best = int(np.argmax(pooled))
    alternative = np.abs(offsets - offsets[best]) >= 0.10
    if (
        best in (0, len(offsets) - 1)
        or scores[best].min() < 0.59
        or np.max(np.abs(offsets[np.argmax(scores, axis=0)] - offsets[best])) > 0.10
        or pooled[best] - pooled[alternative].max() < 0.009
    ):
        raise ValueError("Timing is unclear. Move your hand smoothly and change direction a few times.")
    y0, y1, y2 = pooled[best - 1 : best + 2]
    peak = 2 * y1 - y0 - y2
    refine = np.clip((y2 - y0) / (2 * peak), -0.5, 0.5) if peak > 1e-8 else 0
    return float(offsets[best] + 0.01 * refine)
