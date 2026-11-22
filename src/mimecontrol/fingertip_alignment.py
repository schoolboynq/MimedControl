"""Estimate stereo pose from a hand seen moving through the shared view.

Metric scale comes from the hand itself, so no camera measurements are needed.
"""

from itertools import pairwise

import cv2
import numpy as np

from .calibration import palm_scale
from .stereo import StereoProfile

MIN_SAMPLES = 24
TIP = 8
PALM = (0, 5, 9, 13, 17)
FINGERTIPS = (4, 8, 12, 16, 20)
TRACE_POINTS = (TIP, *PALM)
OPEN_HAND_POINTS = (*PALM, *FINGERTIPS)
# Palm spans and wrist-to-tip spans stay approximately constant as the hand turns.
HAND_SPANS = np.array([(0, 1), (0, 3), (1, 4), *((0, i) for i in range(5, 10))])
SPAN_WEIGHTS = np.array([1, 1, 1, 0.45, 0.45, 0.45, 0.45, 0.45])
# Typical adult wrist-to-middle-knuckle length. It sets the metric scale of the
# fit in place of a measured lens spacing. Depth mapping only uses ratios, so an
# average hand is accurate enough.
PALM_LENGTH_M = 0.093
WRIST, MIDDLE_KNUCKLE = 0, 9


class FingertipAlignment:
    def __init__(self, *, volume=False, automatic=False):
        self.automatic = automatic
        self.indices = OPEN_HAND_POINTS if automatic else TRACE_POINTS
        self.minimum = 18 if automatic else MIN_SAMPLES
        self.sizes = None
        self.near = []
        self.middle = []
        self.far = []
        self.near_scale = None
        self.layers = (self.near, self.middle, self.far) if volume else (self.near, self.far)

    def observe(self, first, second, layer=None):
        views = (first, second)
        sizes = tuple((sample.width, sample.height) for sample in views)
        if self.sizes is not None and self.sizes != sizes:
            raise ValueError("Camera resolution changed. Choose Start over.")
        if any(sample.points is None for sample in views):
            return "Show one hand with your index finger extended in both previews."

        points = np.array([sample.points[list(self.indices), :2] for sample in views], dtype=float)
        scale = np.array([palm_scale(sample.points, sample.width) for sample in views])
        normalized = (points if self.automatic else points[:, 0]) / np.array(sizes).reshape(
            (2, 1, 2) if self.automatic else (2, 2)
        )
        if (
            not np.isfinite(points).all()
            or not np.isfinite(scale).all()
            or np.any(scale <= 0)
            or np.any(normalized < 0.025)
            or np.any(normalized > 0.975)
        ):
            return "Keep your fingertip inside both previews."
        self.sizes = sizes

        target = (
            self.layers[layer] if layer is not None else (self.near if self.near_scale is None else self.far)
        )
        if layer is None and target is self.far and np.any(scale > 0.87 * self.near_scale):
            return "Move your whole hand farther from both cameras and keep it facing them."
        distance = 0.018 if layer is not None else 0.024
        if len(target) < 130 and (
            not target
            or min(np.linalg.norm(normalized - item[0], axis=-1).max() for item in target) >= distance
        ):
            # Store native coordinates. The camera previews and tracker are mirrored.
            points[:, :, 0] = np.array(sizes)[:, None, 0] - 1 - points[:, :, 0]
            target.append((normalized, points, scale))
        if layer is not None:
            return ""
        if len(target) >= MIN_SAMPLES and self.covered(target):
            if target is self.near:
                self.near_scale = np.median([item[2] for item in target], axis=0)
                return "Now move your hand back and sweep up, down and side to side again."
            return "Both sweeps captured. Choose Save alignment."
        if len(target) >= 100:
            del target[0]
        phase = "Near" if target is self.near else "Far"
        if len(target) >= MIN_SAMPLES:
            return f"{phase} sweep · Cover more of the area visible to both cameras."
        return f"{phase} sweep · {len(target)}/{MIN_SAMPLES}. Move slowly up, down and side to side."

    @property
    def complete(self):
        return all(len(layer) >= self.minimum and self.covered(layer) for layer in self.layers)

    def covered(self, samples):
        coordinates = np.array([item[0] for item in samples])
        if self.automatic:
            # A fingertip can travel a wide arc while the palm stays nearly still.
            # Center each landmark's track so a large but stationary hand cannot pass.
            tracks = coordinates - coordinates.mean(axis=0)
            return all(
                np.linalg.svd(view.reshape(-1, 2), compute_uv=False)[-1] >= 0.020 * np.sqrt(view.size / 2)
                for view in tracks.swapaxes(0, 1)
            )
        # Require area coverage, including in the second camera, rather than one straight stroke.
        for view in coordinates.transpose(1, 0, 2):
            if np.linalg.svd(view - view.mean(axis=0), compute_uv=False)[-1] < 0.045 * np.sqrt(len(view)):
                return False
        return True

    def solve(self, secondary):
        if not self.complete:
            raise ValueError("Finish the fingertip traces before saving.")
        samples = [item for layer in self.layers for item in layer]
        points = np.array([item[1] for item in samples])
        size_samples = np.array([item[2] for item in samples])
        layers = np.repeat(np.arange(len(self.layers)), [len(layer) for layer in self.layers])
        holdout = np.arange(len(points)) % 5 == 0
        retry = "The two views didn't line up. Choose Start over and keep your fingertip visible in both views."
        training = points[~holdout]
        if not self.automatic:
            return self.solve_candidate(secondary, points, size_samples, layers, holdout, None)
        seeds = estimate_layouts(training, self.sizes)
        solutions = []
        for seed in seeds:
            try:
                solutions.append(self.solve_candidate(secondary, points, size_samples, layers, holdout, seed))
            except (ValueError, cv2.error, np.linalg.LinAlgError):
                pass
        if not solutions:
            raise ValueError(retry)
        return min(solutions, key=lambda solution: solution[1])

    def solve_candidate(self, secondary, points, size_samples, layers, holdout, seed):
        units = 640 / np.array(self.sizes)[:, 0]
        retry = "The two views didn't line up. Choose Start over and keep your fingertip visible in both views."
        training = points[~holdout]
        k1, k2, rotation, translation = fit_pose(
            training[:, 0], training[:, 1], self.sizes, size_samples[~holdout], seed, self.automatic
        )
        translation *= self.hand_baseline(points, k1, k2, rotation, translation)
        checks = points[holdout].transpose(1, 0, 2, 3).reshape(2, -1, 2)
        left_hold, right_hold = [(view - k[:2, 2]) / k[0, 0] for view, k in zip(checks, (k1, k2))]
        with np.errstate(divide="ignore", invalid="ignore"):
            h = cv2.triangulatePoints(
                np.eye(3, 4), np.column_stack((rotation, translation)), left_hold.T, right_hold.T
            )
            xyz = (h[:3] / h[3]).T
            other = xyz @ rotation.T + translation
            errors = [
                np.linalg.norm((pos[:, :2] / pos[:, 2:] - view) * (k[0, 0] * unit), axis=1)
                for pos, view, k, unit in zip((xyz, other), (left_hold, right_hold), (k1, k2), units)
            ]
        count = len(self.indices)
        residuals = np.maximum(*errors).reshape(-1, count)
        xyz = xyz.reshape(-1, count, 3)
        other = other.reshape(-1, count, 3)
        depths, second_depths = (
            np.median(view[:, :, 2], axis=1) if self.automatic else view[:, 0, 2] for view in (xyz, other)
        )
        primary_error = np.sort(residuals, axis=1)[:, -2] if self.automatic else residuals[:, 0]
        good = (
            (primary_error <= (4.0 if self.automatic else 3.5))
            & (np.median(residuals, axis=1) <= 3.5)
            & (depths > 0.12)
            & (second_depths > 0.12)
            & (depths < 3.0)
            & (second_depths < 3.0)
        )
        checked_layers = [good & (layers[holdout] == index) for index in range(len(self.layers))]
        if np.mean(good) < 0.75 or any(np.count_nonzero(layer) < 3 for layer in checked_layers):
            raise ValueError(retry)
        medians = [np.median(depths[layer]) for layer in checked_layers]
        if any(following < 1.06 * previous for previous, following in pairwise(medians)):
            raise ValueError(
                "The traces were too close in depth. Start over and move farther back when the dot changes planes."
            )
        return StereoProfile(
            secondary, self.sizes, k1, np.zeros(5), k2, np.zeros(5), rotation, translation, approximate=True
        ), float(np.median(residuals[good]))


    def hand_baseline(self, points, k1, k2, rotation, unit):
        """Scale a unit-baseline fit so the median reconstructed palm has typical length."""
        views = points.transpose(1, 0, 2, 3).reshape(2, -1, 2)
        left, right = [(view - k[:2, 2]) / k[0, 0] for view, k in zip(views, (k1, k2))]
        with np.errstate(divide="ignore", invalid="ignore"):
            h = cv2.triangulatePoints(np.eye(3, 4), np.column_stack((rotation, unit)), left.T, right.T)
            xyz = (h[:3] / h[3]).T.reshape(len(points), len(self.indices), 3)
        wrist, knuckle = (self.indices.index(i) for i in (WRIST, MIDDLE_KNUCKLE))
        spans = np.linalg.norm(xyz[:, knuckle] - xyz[:, wrist], axis=1)
        valid = np.isfinite(spans) & (spans > 0) & np.all(xyz[:, :, 2] > 0, axis=1)
        if np.count_nonzero(valid) < 0.75 * len(spans):
            raise ValueError(
                "The two views didn't line up. Choose Start over and keep your hand visible in both views."
            )
        baseline = PALM_LENGTH_M / float(np.median(spans[valid]))
        if not 0.02 <= baseline <= 1.0:
            raise ValueError("The cameras look too close together or too far apart. Mount them 3 to 60 cm apart.")
        return baseline


def estimate_layouts(training, sizes):
    """Seed the solver from video correspondences and both common mounting axes."""
    seeds = [None, (np.array([0.0, 1, 0]), np.eye(3))]
    rays = [
        (points.reshape(-1, 2) - (np.array([width, height]) - 1) / 2) / (0.9 * width)
        for points, (width, height) in zip(training.transpose(1, 0, 2, 3), sizes)
    ]
    essential, mask = cv2.findEssentialMat(*rays, np.eye(3), method=cv2.RANSAC, prob=0.995, threshold=0.005)
    if essential is not None and essential.shape == (3, 3):
        _, rotation, translation, _ = cv2.recoverPose(essential, *rays, np.eye(3), mask=mask)
        seeds.insert(0, (-rotation.T @ translation.ravel(), rotation))
    return seeds


def fit_pose(left, right, sizes, hand_sizes, seed, open_hand):
    # Put the camera centres side by side in the initial estimate. The sweeps
    # refine their relative rotation, offset and focal lengths.
    prior = 0.9 * np.array(sizes)[:, 0]
    center = (np.array(sizes) - 1) / 2
    units = 640 / np.array(sizes)[:, 0]
    centered = [
        np.column_stack(((view - c).reshape(-1, 2), np.ones(view.shape[0] * view.shape[1])))
        for view, c in zip((left, right), center)
    ]
    state = np.zeros(7)
    if seed is None:
        direction = np.array([1.0, 0, 0])
        tangents = np.array([[0, 1.0, 0], [0, 0, 1.0]])
        rotation_seed = np.eye(3)
        direction_sign = 1
        penalties = (1.0, 36.0, 11.0, 11.0)
        bounds = np.array([0.65, 0.75, 0.5, 1.2, 0.6, 0.62, 0.62])
    else:
        direction, rotation_seed = seed
        direction = direction / np.linalg.norm(direction)
        # An inferred baseline may point almost straight along the viewing axis.
        reference = np.eye(3)[np.argmin(np.abs(direction))]
        across = np.cross(reference, direction)
        across /= np.linalg.norm(across)
        tangents = np.array([across, np.cross(direction, across)])
        direction_sign = -1
        penalties = (4.0, 6.0, 11.0, 11.0)
        bounds = np.array([0.8, 0.85, 0.65, 0.8, 0.55, 0.62, 0.62])

    def model(parameters):
        rotation = cv2.Rodrigues(parameters[:3])[0] @ rotation_seed
        offset = direction + parameters[3:5] @ tangents
        unit = direction_sign * (rotation @ (offset / np.linalg.norm(offset)))
        focal = prior * np.exp(parameters[5:])
        rays = [view / (f, f, 1) for view, f in zip(centered, focal)]
        return rotation, unit, focal, rays

    def residual(parameters, palm_weight):
        rotation, (x, y, z), focal, (a, b) = model(parameters)
        essential = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]]) @ rotation
        ea, eb = a @ essential.T, b @ essential
        pixel_focal = focal * units
        normal = np.sqrt(
            np.sum((ea[:, :2] / pixel_focal[1]) ** 2, axis=1)
            + np.sum((eb[:, :2] / pixel_focal[0]) ** 2, axis=1)
        )
        error = np.sum(b * ea, axis=1) / np.maximum(normal, 1e-10)
        # Soft constraints keep nearly parallel camera fits from drifting to an
        # implausible lens or to a camera placed far in front of the other.
        regularization = parameters[3:] * penalties
        if not palm_weight:
            return np.r_[error, regularization]
        # Use the midpoint of the shortest segment between the two rays.
        # The guided trace only needs wrist depth for its apparent palm scale.
        if not open_hand:
            a, b = a[1 :: len(TRACE_POINTS)], b[1 :: len(TRACE_POINTS)]
        b = b @ rotation
        camera_center = -rotation.T @ np.array([x, y, z])
        aa = np.sum(a**2, axis=1)
        bb = np.sum(b**2, axis=1)
        ab = np.sum(a * b, axis=1)
        ac, bc = a @ camera_center, b @ camera_center
        denom = np.maximum(1e-10, aa * bb - ab**2)
        first_depth = (ac * bb - ab * bc) / denom
        second_depth = (ab * ac - aa * bc) / denom
        shape = np.empty(0)
        if open_hand:
            points = ((a * first_depth[:, None] + b * second_depth[:, None] + camera_center) * 0.5).reshape(
                -1, len(OPEN_HAND_POINTS), 3
            )
            span = np.linalg.norm(points[:, HAND_SPANS[:, 0]] - points[:, HAND_SPANS[:, 1]], axis=2)
            log_span = np.log(np.maximum(span, 1e-6))
            change = log_span - np.median(log_span, axis=0)
            # Wide spans constrain pose, while the palm scale also anchors depth
            # when parallax is small and a pixel of jitter moves a joint along its ray.
            shape = (0.50 * palm_weight * change * SPAN_WEIGHTS).ravel()
            first_depth = first_depth[:: len(OPEN_HAND_POINTS)]
            second_depth = second_depth[:: len(OPEN_HAND_POINTS)]
        apparent = hand_sizes * np.abs(np.column_stack((first_depth, second_depth)))
        palm = np.log(np.maximum(apparent, 1e-6))
        palm -= np.median(palm, axis=0)
        return np.r_[error, palm_weight * palm.ravel(), shape, regularization]

    steps = np.eye(7) * 1e-5
    for palm_weight in (0, 7):
        damping = 0.01
        for _ in range(45):
            error = residual(state, palm_weight)
            weight = 1 / np.sqrt(1 + (error / 2.5) ** 2)
            jacobian = np.column_stack([(residual(state + step, palm_weight) - error) / 1e-5 for step in steps])
            weighted = jacobian.T * weight
            delta = np.linalg.solve(weighted @ jacobian + damping * np.eye(7), weighted @ error)
            candidate = np.clip(state - delta, -bounds, bounds)
            candidate_error = residual(candidate, palm_weight)
            if np.hypot(2.5, candidate_error).sum() < np.hypot(2.5, error).sum():
                state = candidate
                damping = max(0.00001, damping / 4)
                if np.linalg.norm(delta) < 1e-6:
                    break
            else:
                damping = min(1e6, damping * 8)
    rotation, translation, focal, rays = model(state)
    h = cv2.triangulatePoints(
        np.eye(3, 4), np.column_stack((rotation, translation)), rays[0][:, :2].T, rays[1][:, :2].T
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        if np.median(h[2] / h[3]) < 0:
            translation = -translation
    cameras = [np.array([[f, 0, x], [0, f, y], [0, 0, 1]]) for f, (x, y) in zip(focal, center)]
    return cameras[0], cameras[1], rotation, translation
