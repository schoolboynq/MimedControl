"""Fit a camera pair from landmark tracks of a turning hand. No measurements are needed."""

from dataclasses import replace

import numpy as np

from .calibration import palm_scale
from .fingertip_alignment import FINGERTIPS, PALM, FingertipAlignment
from .stereo_timing import estimate_motion_offset

VISIBILITY_HINT = "Show an open palm with all five fingertips in both previews."
DEPTH_HINT = "Move your open hand closer, then farther away, and turn it at each distance."
ROTATION_HINT = "Turn your palm left and right and tilt it at each distance. Keep all five fingertips visible."
SAME_HAND_HINT = "Keep both hands visible to both cameras, or use one hand."


def hand_side(sample):
    """0 for one hand, otherwise -1 or 1 for the tracked hand's side of the other hand.

    Both previews are mirrored and face the user, so side-by-side hands keep
    their left-to-right order in each view. That identifies the same hand.
    """
    if sample.second is None:
        return 0
    return -1 if sample.points[list(PALM), 0].mean() < sample.second[list(PALM), 0].mean() else 1


def open_hand(sample):
    if sample.points is None:
        return False
    points = sample.points[:, :2]
    frame = np.array((sample.width, sample.height))
    if not np.isfinite(sample.points).all() or not ((points > 0.015 * frame) & (points < 0.985 * frame)).all():
        return False

    fingers = points[5:].reshape(4, 4, 2)
    reach = np.linalg.norm(fingers[:, -1] - fingers[:, 0], axis=1)
    chain = np.linalg.norm(np.diff(fingers, axis=1), axis=2).sum(axis=1)
    palm = points[9] - points[0]
    across = points[17] - points[5]
    breadth = np.linalg.norm(across)
    span = max(np.linalg.norm(palm), breadth)
    area = abs(palm[0] * across[1] - palm[1] * across[0])
    separation = np.linalg.norm(np.diff(points[list(FINGERTIPS)], axis=0), axis=1)
    return bool(
        span >= 10
        and area >= 0.22 * span**2
        and np.all(reach >= np.maximum(0.33 * span, 0.76 * chain))
        and np.all(separation >= 0.19 * span)
        and separation[0] >= 0.35 * span
        and np.linalg.norm(points[20] - points[8]) >= 1.10 * breadth
        and np.all(np.sum((fingers[:, -1] - fingers[:, 0]) * (fingers[:, 0] - points[0]), axis=1) > 0)
    )


def hand_features(sample):
    palm = sample.points[list(PALM), :2]
    center = palm.mean(axis=0) / (sample.width, sample.height)
    scale = palm_scale(sample.points, sample.width)
    # Relative vectors from the palm to widely spaced tips capture rotation even
    # when the palm centre hardly moves. Normalizing by palm scale separates depth.
    tips = sample.points[[4, 12, 20], :2]
    axes = np.array([tips[1] - palm[0], tips[2] - tips[0]]) / max(1, scale * sample.width)
    return np.r_[center, 0.28 * np.log(max(1e-8, scale)), 0.20 * axes.ravel()]


def other_hand(frame):
    """The same frame with its second hand listed first, or None without one."""
    if frame.second is None:
        return None
    return replace(frame, points=frame.second, second=frame.points, world=frame.second_world, second_world=frame.world)


def solve_video_tracks(first, second, secondary, both_hands=False):
    """Use synchronized landmark samples pulled from both live videos.

    Timing comes from the first-listed hand's track. With both_hands, the other
    hand's views are paired at the same timing and add to the fit, which spreads
    the samples across both cameras' views.
    """
    all_first, all_second = first, second
    first, second = (tuple(frame for frame in view if open_hand(frame)) for view in (first, second))
    if min(len(first), len(second)) < 85:
        raise ValueError(VISIBILITY_HINT)
    sizes = tuple((view[-1].width, view[-1].height) for view in (first, second))
    if any((frame.width, frame.height) != size for view, size in zip((first, second), sizes) for frame in view):
        raise ValueError("A camera changed resolution. Choose Start over.")

    sensor = (
        all(frame.sensor_time is not None for view in (first, second) for frame in view)
        and abs(first[0].sensor_time - second[0].sensor_time) < 1
    )
    clock = "sensor" if sensor else "arrival"

    def stamp(frame):
        return frame.sensor_time if sensor else frame.timestamp

    signal = [
        np.array([np.r_[stamp(frame), hand_features(frame)] for frame in view]) for view in (first, second)
    ]
    for track in signal:
        if not np.isfinite(track).all() or np.any(np.diff(track[:, 0]) <= 0):
            raise ValueError("The camera timestamps changed. Choose Start over.")
        size_band = np.percentile(track[:, 3], (20, 80))
        if size_band[1] - size_band[0] < 0.28 * np.log(1.20):
            raise ValueError(DEPTH_HINT)
        # Check pose diversity within depth bands so translation alone cannot pass.
        bands = np.array_split(track[np.argsort(track[:, 3]), 4:], 3)
        turns = [np.linalg.norm(poses - np.median(poses, axis=0), axis=1) for poses in bands]
        if sum(np.percentile(turn, 85) >= 0.064 for turn in turns) < 2:
            raise ValueError(ROTATION_HINT)

    offset = estimate_motion_offset(*signal)
    pairs = pair_video_frames(first, second, offset, clock)
    if len(pairs) < 75:
        if any(hand_side(frame) for view in (first, second) for frame in view):
            raise ValueError(SAME_HAND_HINT)
        raise ValueError("The cameras need more shared motion. Keep your hand visible to both.")
    if both_hands:
        others = [tuple(hand for hand in map(other_hand, view) if hand is not None and open_hand(hand))
                  for view in (all_first, all_second)]
        if min(map(len, others)) >= 20:
            pairs += pair_video_frames(*others, offset, clock)
    samples = sorted(pairs, key=lambda item: -item[0])
    alignment = FingertipAlignment(volume=True, automatic=True)
    for index, indices in enumerate(np.array_split(np.arange(len(samples)), 3)):
        # Split by apparent depth. Retain capture order inside each band for
        # held-out validation, so one part of the motion cannot dominate.
        band = sorted((samples[i] for i in indices), key=lambda pair: pair[1].timestamp)
        for _, left, right in band:
            alignment.observe(left, right, layer=index)
    if not alignment.complete:
        raise ValueError("Move your open hand a little side to side as you turn at each distance.")
    try:
        profile, _ = alignment.solve(secondary)
    except ValueError as exc:
        raise ValueError("The views don't agree yet. Rotate more slowly at three distinct distances.") from exc
    return replace(profile, time_offset_s=offset, sync_clock=clock)


def pair_video_frames(first, second, offset, clock):
    """Interpolate landmarks only between close, visible frames of the other camera."""
    sensor = clock == "sensor"

    def stamp(frame):
        return frame.sensor_time if sensor else frame.timestamp

    if sensor and any(frame.sensor_time is None for view in (first, second) for frame in view):
        return []
    second_times = [stamp(frame) for frame in second]
    pairs = []
    last = -np.inf
    for a in first:
        t = stamp(a)
        # Cap the fit at about 16 well-spaced observations per second.
        if t - last < 0.057:
            continue
        target = t + offset
        index = np.searchsorted(second_times, target)
        if not 0 < index < len(second_times):
            continue
        low, high = second[index - 1], second[index]
        gap = stamp(high) - stamp(low)
        if not 0 < gap <= 0.10 or not hand_side(a) == hand_side(low) == hand_side(high):
            continue
        blend = (target - stamp(low)) / gap
        b = replace(low, points=low.points + blend * (high.points - low.points))
        scale = [palm_scale(s.points, s.width) for s in (a, b)]
        pairs.append((np.sqrt(scale[0] * scale[1]), a, b))
        last = t
    return pairs
