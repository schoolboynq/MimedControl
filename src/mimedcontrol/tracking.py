"""Run the OpenCV Zoo palm and hand-landmark models on the CPU."""

from math import atan2, ceil, degrees, hypot
from pathlib import Path

import cv2
import numpy as np

MODELS = Path(__file__).with_name("models")
PALM_SIZE = 192
HAND_SIZE = 224
PALM_LANDMARKS = [0, 5, 9, 13, 17, 1, 2]
PALM_BASES = PALM_LANDMARKS[:5]
PALM_SCAN_ANGLES = (0, 90, -90, 180, 45, -45, 135, -135)
PALM_TILE_ANGLES = (0, 90, -90, 0, 180, 0, 45, 0, -45, 0, 135, 0, -135)


def palm_region(points):
    bases = points[PALM_BASES, :2]
    length = np.linalg.norm(bases[2] - bases[0])
    width = np.linalg.norm(bases[4] - bases[1])
    radius = max(7, 0.70 * max(length, width))
    return bases.mean(axis=0), radius


def palm_transform(width, height, angle):
    rotation = cv2.getRotationMatrix2D((0, 0), angle, 1)[:, :2]
    dimensions = np.array([width, height])
    # Fit every corner, including at diagonal rotations and in a wide camera frame.
    linear = rotation * (PALM_SIZE / np.max(np.abs(rotation) @ dimensions))
    return np.column_stack((linear, PALM_SIZE / 2 - linear @ (dimensions / 2)))


def centered_region(width, height, center, side):
    region_width, region_height = min(width, side), min(height, side)
    left = min(width - region_width, max(0, round(center[0] - region_width / 2)))
    top = min(height - region_height, max(0, round(center[1] - region_height / 2)))
    return left, top, left + region_width, top + region_height


def tile_origins(length, side):
    last = length - side
    if last <= 0:
        return (0,)
    steps = ceil(last / max(1, side * 0.80))  # Keep adjacent tiles overlapping.
    return tuple(round(last * i / steps) for i in range(steps + 1))


def palm_scan_plan(width, height, *, waiting=False):
    side = max(1, round(0.44 * max(width, height)))
    center = centered_region(width, height, (width / 2, height / 2), side)
    tile_width, tile_height = min(width, side), min(height, side)
    regions = [(x, y, x + tile_width, y + tile_height) for y in tile_origins(height, tile_height)
               for x in tile_origins(width, tile_width)]
    tiles = [(None, region) for region in regions if region != center]
    # A None angle lets the magnified probe rotate on a later sweep. Keep the ordinary
    # full and center views in the cycle for hands that enter after a longer absence.
    if waiting:
        return ((0, None), (0, center), (None, center), *tiles[:3], (0, center), *tiles[3:], (None, None))
    return ((0, None), (None, center), (90, None), *tiles[:3],
            (0, center), *tiles[3:], (0, None), (0, center),
            *((angle, None) for angle in PALM_SCAN_ANGLES[2:5]), (0, center),
            *((angle, None) for angle in PALM_SCAN_ANGLES[5:]))


class PalmScan:
    def __init__(self):
        self.reset()

    def reset(self):
        self.index = 0
        self.hint = None
        self.tile_rotation = 0
        self.swept = False

    def next_probe(self, plan):
        if self.hint is not None:
            probe, self.hint = self.hint, None
            return probe
        angle, region = plan[self.index]
        if angle is None:
            angle = PALM_TILE_ANGLES[self.tile_rotation]
        self.index = (self.index + 1) % len(plan)
        if self.index == 0:
            self.tile_rotation = (self.tile_rotation + 1) % len(PALM_TILE_ANGLES)
            self.swept = True
        return angle, region


def palm_anchors():
    # The palm model uses two anchors per 24×24 cell and six per 12×12 cell.
    grids = []
    for size, copies in ((24, 2), (12, 6)):
        centers = (np.arange(size, dtype=np.float32) + 0.5) / size
        cells = np.stack(np.meshgrid(centers, centers), axis=-1).reshape(-1, 2)
        grids.append(np.repeat(cells, copies, axis=0))
    return np.concatenate(grids)


class HandTracker:
    def __init__(self):
        cv2.setNumThreads(1)
        self.palm = cv2.dnn.readNetFromONNX(str(MODELS / "palm.onnx"))
        self.hand = cv2.dnn.readNetFromONNX(str(MODELS / "hand.onnx"))
        self.palm_outputs = self.palm.getUnconnectedOutLayersNames()
        self.hand_outputs = self.hand.getUnconnectedOutLayersNames()
        self.anchors = palm_anchors()
        self.frame_size = None
        self.first_scan, self.second_scan = PalmScan(), PalmScan()
        self.clear_tracks()

    def clear_tracks(self):
        self.landmarks = None
        self.world_landmarks = None
        self.second_landmarks = self.second_world_landmarks = None
        self.skip_detection = 0
        self.second_countdown = 0
        self.first_scan.reset()
        self.second_scan.reset()
        self.first_wide_crop = self.second_wide_crop = False

    def detect_palm(self, frame, exclude=None, angle=0, region=None):
        left, top = 0, 0
        if region is not None:
            left, top, right, bottom = region
            frame = frame[top:bottom, left:right]
        height, width = frame.shape[:2]
        transform = palm_transform(width, height, angle)
        crop = cv2.warpAffine(frame, transform, (PALM_SIZE, PALM_SIZE))
        self.palm.setInput(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)[None].astype(np.float32) / 255)
        boxes, scores = self.palm.forward(self.palm_outputs)
        scores = scores.reshape(-1)
        indices = np.flatnonzero(scores >= 0.40)  # Logit for a probability of about 0.60.
        if indices.size == 0:
            return None
        points = boxes[0, indices, 4:].reshape(-1, 7, 2) + self.anchors[indices, None, :] * PALM_SIZE
        inverse = cv2.invertAffineTransform(transform)
        points = points @ inverse[:, :2].T + inverse[:, 2] + (left, top)
        if exclude is not None:
            center, radius = palm_region(exclude)
            distinct = np.linalg.norm(points[:, :5].mean(axis=1) - center, axis=1) > radius
            if not distinct.any():
                return None
            return points[distinct][np.argmax(scores[indices[distinct]])]
        return points[np.argmax(scores[indices])]

    def estimate_landmarks(self, frame, palm, previous=None, wide_crop=False):
        up = palm[2] - palm[0]
        length = np.linalg.norm(up)
        if length >= 4:
            up /= length
        else:
            # An end-on wrist can project near the knuckles. Use the knuckle line for the axis.
            across = palm[4] - palm[1]
            width = np.linalg.norm(across)
            if width < 4:
                return None
            direction = np.array([-across[1], across[0]]) / width
            direction_to_fingers = palm[1:5].mean(axis=0) - palm[0]
            up = direction if direction @ direction_to_fingers >= 0 else -direction
        axes = np.array([[-up[1], up[0]], -up])
        roi = palm if previous is None else previous[:, :2]
        projected = roi @ axes.T
        low, high = projected.min(axis=0), projected.max(axis=0)
        extent = high - low
        center = (low + high) * 0.5
        if previous is None:
            center[1] -= extent[1] * 0.40
            edge = extent.max() * 3.0
        else:
            center[1] -= extent[1] * 0.08
            edge = max(extent.max() * 1.65, length * 2.4)
        if edge < 12:
            return None

        # The alternate crop gives foreshortened fingers more context and transverse pixels.
        scales = np.full(2, HAND_SIZE / edge)
        if wide_crop:
            scales *= np.array([1.25, 1.0]) / 1.2
        transform = np.column_stack((axes * scales[:, None], HAND_SIZE / 2 - center * scales))
        crop = cv2.warpAffine(frame, transform, (HAND_SIZE, HAND_SIZE))
        self.hand.setInput(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)[None].astype(np.float32) / 255)
        coords, confidence, _, world_coords = self.hand.forward(self.hand_outputs)
        if float(confidence.item()) < 0.75:
            return None
        points = coords.reshape(21, 3)
        inverse = cv2.invertAffineTransform(transform)
        points[:, :2] = points[:, :2] @ inverse[:, :2].T + inverse[:, 2]
        points[:, 2] /= np.sqrt(scales[0] * scales[1])
        if not np.isfinite(points).all():
            return None
        world = world_coords.reshape(21, 3)
        return points, world if np.isfinite(world).all() else None

    def observe(self, frame, previous, other=None, angle=0, wide_crop=False, region=None):
        if previous is None:
            palm = self.detect_palm(frame, exclude=other, angle=angle, region=region)
        else:
            palm = previous[PALM_LANDMARKS, :2]
        if palm is None:
            return None, None, False
        hand = self.estimate_landmarks(frame, palm, previous, wide_crop)
        if hand is None:
            wide_crop = not wide_crop
            hand = self.estimate_landmarks(frame, palm, previous, wide_crop)
        if hand is None:
            return None, None, False
        points, world = hand
        if other is not None:
            center, _ = palm_region(points)
            other_center, radius = palm_region(other)
            if np.linalg.norm(center - other_center) < radius:
                return None, None, False
        return points, world, wide_crop

    def observe_next(self, frame, previous, other, scan, wide_crop):
        plan = self.waiting_plan if scan.swept else self.scan_plan
        angle, region = scan.next_probe(plan) if previous is None else (0, None)
        current, world, wide_crop = self.observe(frame, previous, other, angle, wide_crop, region)
        if current is not None:
            scan.reset()
        elif previous is not None:
            width, height = self.frame_size
            center, radius = palm_region(previous)
            side = round(max(0.36 * max(width, height), radius * 9))
            upward_x, upward_y = previous[9, :2] - previous[0, :2]
            angle = round(degrees(atan2(upward_x, -upward_y)) / 90) * 90 if hypot(upward_x, upward_y) >= 4 else 0
            scan.hint = angle, centered_region(width, height, center, side)
        return current, world, wide_crop

    def track(self, frame, two_hands=False, eager_second=False):
        frame_size = frame.shape[1], frame.shape[0]
        if frame_size != self.frame_size:
            self.clear_tracks()
            self.frame_size = frame_size
            self.scan_plan = palm_scan_plan(*frame_size)
            self.waiting_plan = palm_scan_plan(*frame_size, waiting=True)
        if not two_hands:
            self.second_landmarks = self.second_world_landmarks = None
            self.second_countdown = 0
            self.second_scan.reset()
            self.second_wide_crop = False

        searched = False
        if self.landmarks is None and self.skip_detection:
            self.skip_detection -= 1
        else:
            previous = self.landmarks
            used_hint = self.first_scan.hint is not None
            searched = previous is None
            self.landmarks, self.world_landmarks, self.first_wide_crop = self.observe_next(
                frame, previous, self.second_landmarks, self.first_scan, self.first_wide_crop
            )
            if self.landmarks is None and previous is None:
                # Give the leading center probe and recovery from a missed local hint the next frame.
                if used_hint:
                    self.skip_detection = 0
                elif self.first_scan.swept:
                    self.skip_detection = 3
                else:
                    self.skip_detection = 0 if self.first_scan.index == 1 else 1

        if two_hands:
            if eager_second and self.second_landmarks is not None:
                self.second_countdown = 0
            if self.second_countdown:
                self.second_countdown -= 1
            elif self.second_landmarks is not None or (self.landmarks is not None and not searched):
                self.second_landmarks, self.second_world_landmarks, self.second_wide_crop = self.observe_next(
                    frame, self.second_landmarks, self.landmarks, self.second_scan, self.second_wide_crop
                )
                # The pointer still gets an inference every frame. Refresh the other hand less often,
                # except while calibrating with both hands, when each hand is a corner of the box.
                if eager_second:
                    self.second_countdown = 0 if self.second_landmarks is not None else 2
                else:
                    self.second_countdown = 2 if self.second_landmarks is not None or self.second_scan.hint else 5
        return self.landmarks
