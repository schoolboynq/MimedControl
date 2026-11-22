"""Calibrate a fixed camera pair and recover metric hand joints in its shared view."""

import json
from dataclasses import dataclass, replace
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np

from .calibration import profile_path
from .stereo_timing import MAX_OFFSET

PAIR_WINDOW = 0.040


def time_difference(left, right, offset=0.0, clock=""):
    sensor = left.sensor_time is not None and right.sensor_time is not None
    if clock == "arrival":
        return abs(right.timestamp - left.timestamp - offset)
    if sensor:
        return abs(right.sensor_time - left.sensor_time - offset)
    if clock == "sensor":
        return float("inf")
    return abs(right.timestamp - left.timestamp)


@dataclass
class StereoProfile:
    secondary: int
    sizes: tuple
    k1: np.ndarray
    d1: np.ndarray
    k2: np.ndarray
    d2: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    enabled: bool = True
    approximate: bool = False
    time_offset_s: float = 0.0
    sync_clock: str = ""
    calibration_runs: int = 1

    def triangulate(self, left, right):
        hit = self.triangulate_with_error(left, right)
        return None if hit is None else hit[0]

    def triangulate_with_error(self, left, right):
        """Hand joints in the primary camera's frame and their median pixel error, or None."""
        import cv2

        observations = []
        for points, (width, _), k, d in zip((left, right), self.sizes, (self.k1, self.k2), (self.d1, self.d2)):
            # Tracking mirrors each image horizontally. Calibration uses native optics.
            pixels = np.array(points[:, :2], dtype=np.float64)
            pixels[:, 0] = width - 1 - pixels[:, 0]
            observations.append(cv2.undistortPoints(pixels[:, None], k, d).reshape(-1, 2))
        first = np.eye(3, 4)
        other = np.column_stack((self.rotation, self.translation))
        h = cv2.triangulatePoints(first, other, observations[0].T, observations[1].T)
        if np.any(np.abs(h[3]) < 1e-8):
            return None
        xyz = (h[:3] / h[3]).T
        transformed = xyz @ self.rotation.T + self.translation
        if (
            not np.isfinite(xyz).all()
            or np.any(xyz[:, 2] < 0.08)
            or np.any(transformed[:, 2] < 0.08)
            or np.linalg.norm(self.translation) / np.median(xyz[:, 2]) < 0.025
        ):
            return None
        # Reject mismatched hands, occluded tips and badly timed views. Compare
        # residuals in pixels of each camera and reject implausible hand geometry.
        residual = 0.0
        for p, observed, k in zip((xyz, transformed), observations, (self.k1, self.k2)):
            errors = np.linalg.norm((p[:, :2] / p[:, 2:] - observed) * (k[0, 0], k[1, 1]), axis=1)
            if np.median(errors) > 2.5 or errors.max() > 5:
                return None
            residual += float(np.median(errors))
        span = np.linalg.norm(xyz[0] - xyz[9])
        chains = xyz[1:].reshape(5, 4, 3)
        bones = np.linalg.norm(np.diff(chains, axis=1), axis=2)
        if not 0.032 <= span <= 0.155 or bones.min() < 0.002 or bones.max() > 0.85 * span:
            return None
        return xyz, residual

    def pair_error(self, primary, auxiliary):
        return time_difference(primary, auxiliary, self.time_offset_s, self.sync_clock)

    def match(self, primary, auxiliary):
        return self.match_hands(primary, auxiliary)[0]

    def match_hands(self, primary, auxiliary):
        """Triangulate both hands, working out which hand is which in the second view.

        Two hands side by side lie along the same epipolar line, so the left hand in
        one view can pass every check against the right hand in the other at a wrong
        depth. Matching both together and keeping the pairing that explains the most
        hands, then the smallest error, avoids that. Returns (tracked hand, other hand)
        joint arrays, each None when it can't be matched without ambiguity.
        """
        if (
            primary.points is None
            or auxiliary is None
            or auxiliary.points is None
            or abs(primary.timestamp - auxiliary.timestamp) > (0.58 if self.sync_clock else 0.12)
            or self.pair_error(primary, auxiliary) > PAIR_WINDOW
            or ((primary.width, primary.height), (auxiliary.width, auxiliary.height)) != self.sizes
        ):
            return None, None
        hands = (primary.points, primary.second)
        views = [view for view in (auxiliary.points, auxiliary.second) if view is not None]
        hits = {}
        for i, hand in enumerate(hands):
            for j, view in enumerate(views):
                if hand is not None:
                    hit = self.triangulate_with_error(hand, view)
                    if hit is not None:
                        hits[i, j] = hit
        choices = []
        for first in (None, *range(len(views))):
            for second in (None, *range(len(views))):
                if first is not None and first == second:
                    continue
                pairs = [(i, j) for i, j in ((0, first), (1, second)) if j is not None]
                if all(pair in hits for pair in pairs):
                    choices.append((len(pairs), sum(hits[pair][1] for pair in pairs), first, second))
        choices.sort(key=lambda choice: (-choice[0], choice[1]))
        if not choices or choices[0][0] == 0:
            return None, None
        count, _, first, second = choices[0]
        rivals = [choice for choice in choices[1:] if choice[0] == count]
        # Equally good pairings that disagree about a hand mean that hand can't be trusted.
        keep_first = first is not None and all(choice[2] == first for choice in rivals)
        keep_second = second is not None and all(choice[3] == second for choice in rivals)
        return (hits[0, first][0] if keep_first else None, hits[1, second][0] if keep_second else None)


def settings_path(primary):
    return profile_path(primary, 0).with_name(f"stereo-{primary}.json")


def save_profile(path, profile):
    data = {
        key: value.tolist() if isinstance(value, np.ndarray) else value for key, value in vars(profile).items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as out:
            temp = Path(out.name)
            json.dump(data, out, allow_nan=False)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def load_profile(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        secondary = data["secondary"]
        if type(secondary) is not int or not 0 <= secondary <= 9 or type(data["enabled"]) is not bool:
            return None
        sizes = tuple(tuple(s) for s in data["sizes"])
        if len(sizes) != 2 or any(len(s) != 2 or any(type(n) is not int or n <= 0 for n in s) for s in sizes):
            return None
        matrices = []
        for key, shape in (
            ("k1", (3, 3)),
            ("d1", (5,)),
            ("k2", (3, 3)),
            ("d2", (5,)),
            ("rotation", (3, 3)),
            ("translation", (3,)),
        ):
            a = np.asarray(data[key], dtype=float)
            if a.shape != shape or not np.isfinite(a).all():
                return None
            matrices.append(a)
        for k in matrices[0:4:2]:
            if k[0, 0] <= 0 or k[1, 1] <= 0:
                return None
        offset, clock = data.get("time_offset_s", 0), data.get("sync_clock", "")
        if clock not in ("", "sensor", "arrival") or not isfinite(offset) or abs(offset) > MAX_OFFSET:
            return None
        runs = data.get("calibration_runs", 1)
        if type(runs) is not int or runs < 1:
            return None
        return StereoProfile(
            secondary,
            sizes,
            *matrices,
            enabled=data["enabled"],
            approximate=data.get("approximate") is True,
            time_offset_s=offset,
            sync_clock=clock,
            calibration_runs=runs,
        )
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def average_profiles(saved, measured):
    """Combine two groups of runs for a fixed camera pair."""
    import cv2

    if saved.secondary != measured.secondary or saved.sizes != measured.sizes:
        raise ValueError("The camera pair or video size changed. Choose Save new alignment.")
    if saved.sync_clock != measured.sync_clock or abs(saved.time_offset_s - measured.time_offset_s) > 0.12:
        raise ValueError("Camera timing changed too much to average. Choose Save new alignment.")
    old_spacing = np.linalg.norm(saved.translation)
    new_spacing = np.linalg.norm(measured.translation)
    # Each run estimates spacing from hand size, so allow for that estimate's spread.
    if min(old_spacing, new_spacing) <= 0 or abs(new_spacing / old_spacing - 1) > 0.15:
        raise ValueError("The camera spacing looks different from the saved pair. Choose Save new alignment.")

    relative_rotation = cv2.Rodrigues(measured.rotation @ saved.rotation.T)[0]
    old_center = -saved.rotation.T @ saved.translation / old_spacing
    new_center = -measured.rotation.T @ measured.translation / new_spacing
    if (
        np.linalg.norm(relative_rotation) > 0.19
        or np.linalg.norm(new_center - old_center) > 0.25
        or any(abs(b[0, 0] / a[0, 0] - 1) > 0.22 for a, b in ((saved.k1, measured.k1), (saved.k2, measured.k2)))
    ):
        raise ValueError(
            "The alignment differs too much from the saved pair. Repeat it or choose Save new alignment."
        )

    runs = saved.calibration_runs + measured.calibration_runs
    fraction = measured.calibration_runs / runs

    def average(a, b):
        return a + fraction * (b - a)

    rotation = cv2.Rodrigues(relative_rotation * fraction)[0] @ saved.rotation
    center = average(old_center, new_center)
    center *= average(old_spacing, new_spacing) / np.linalg.norm(center)
    return replace(
        measured,
        k1=average(saved.k1, measured.k1),
        k2=average(saved.k2, measured.k2),
        d1=average(saved.d1, measured.d1),
        d2=average(saved.d2, measured.d2),
        rotation=rotation,
        translation=-rotation @ center,
        approximate=saved.approximate or measured.approximate,
        time_offset_s=average(saved.time_offset_s, measured.time_offset_s),
        calibration_runs=runs,
    )
