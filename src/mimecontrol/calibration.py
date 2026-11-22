"""Personal reach calibration that tolerates a camera turned or tilted relative to the user."""

import json
import os
import sys
from math import isfinite
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile

import numpy as np


def palm_scale(points, width):
    # The hand model expresses its landmark Z offsets in the same scale as image XY.
    # Three rigid palm spans give an apparent size independent of finger extension.
    bones = points[[5, 17, 17], :3] - points[[0, 0, 5], :3]
    return float(np.linalg.norm(bones)) / width


def hand_point(x, y, scale):
    """Hand position in the camera's frame, up to one fixed linear map.

    Apparent size falls as 1/distance, so (x, y, 1) / scale is the camera-frame
    position scaled by an unknown constant. The unknown focal length and image
    center only add a linear map, which the fit below absorbs. That makes the
    calibration independent of how the camera is turned relative to the user.
    """
    d = 1.0 / scale
    return np.array((x * d, y * d, d))


def homography(source, target):
    """Projective map taking four 2D points exactly onto four others."""
    rows = []
    for (x, y), (u, v) in zip(source, target):
        rows.append((x, y, 1, 0, 0, 0, -u * x, -u * y, -u))
        rows.append((0, 0, 0, x, y, 1, -v * x, -v * y, -v))
    return np.linalg.svd(np.array(rows))[2][-1].reshape(3, 3)


def convex(corners):
    edges = np.roll(corners, -1, axis=0) - corners
    turns = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    return bool(np.all(turns > 0) or np.all(turns < 0))


def apply_homography(matrix, point):
    x, y, w = matrix @ (point[0], point[1], 1.0)
    return np.array((x / w, y / w)) if abs(w) > 1e-9 else np.array(point, dtype=float)


def clipped(value):
    return max(0.0, min(1.0, value))


# Box coordinates: x from your left to right, y from top to bottom, and depth from
# close in to you (0) to arms out toward the screen (1).
CORNERS = ((0, 0), (1, 0), (1, 1), (0, 1))


class Reach:
    """Map the hand to the screen from guided calibration samples.

    A hand's camera-space position, from its image position and apparent size
    (or stereo depth), maps to the cube through one weighted linear fit. Any
    camera tilt or offset is only a linear change of coordinates, so the fit
    absorbs it. A correction at each traced distance then makes your own
    corners land exactly on the screen corners, blended smoothly by depth.

    Samples are (x, y, scale, cube_x, cube_y, depth, weight, kind), where kind
    is "corner" for a held corner and "path" for a point along a straight move.
    """

    def __init__(self, samples, depth_scale=None):
        rows = []
        for sample in samples:
            x, y, scale, u, v, w, weight, kind = sample
            rows.append((float(x), float(y), float(scale), float(u), float(v), float(w), float(weight), str(kind)))
        if depth_scale is not None and not (isfinite(depth_scale) and depth_scale > 0):
            raise ValueError("Invalid stereo depth scale")
        values = np.array([row[:7] for row in rows], dtype=float).reshape(-1, 7)
        if len(rows) < 8 or not np.isfinite(values).all() or np.any(values[:, 2] <= 0) or np.any(values[:, 6] < 0):
            raise ValueError("Invalid reach calibration")
        self.samples = tuple(rows)
        self.depth_scale = depth_scale
        self.passes = None  # The drawings this box came from, so it can be refined later.
        # How much of the drawn box spans the screen: 0.75 needs less movement, 1.25 more.
        self.width = 1.0
        points = np.array([hand_point(*row[:3]) for row in rows])
        targets = values[:, 3:6]
        weights = np.sqrt(values[:, 6])[:, None]
        design = np.column_stack((points, np.ones(len(points))))
        self.linear, _, rank, _ = np.linalg.lstsq(design * weights, targets * weights, rcond=None)
        if rank < 4:
            raise ValueError("Calibration needs more spread. Start over.")
        # Exact corners at each distance that has all four.
        self.corrections = []
        for depth in sorted({row[5] for row in rows if row[7] == "corner"}):
            mapped = []
            for corner in CORNERS:
                hits = [row for row in rows if row[7] == "corner" and row[5] == depth and (row[3], row[4]) == corner]
                if not hits:
                    break
                # Several drawings of a corner count by how recent they are.
                mapped.append(np.average([self.raw(row[:3])[:2] for row in hits], axis=0,
                                         weights=[max(row[6], 1e-9) for row in hits]))
            else:
                if not convex(np.array(mapped)):
                    raise ValueError("The corners crossed over. Reach toward each corner in turn.")
                self.corrections.append((depth, homography(mapped, CORNERS)))
        if not self.corrections:
            raise ValueError("Invalid reach calibration")
        near = np.mean([self.raw(row[:3])[2] for row in rows if row[5] == min(r[5] for r in rows)])
        far = np.mean([self.raw(row[:3])[2] for row in rows if row[5] == max(r[5] for r in rows)])
        if far <= near:
            raise ValueError("The distances came out reversed. Start over.")

    def raw(self, sample):
        return np.r_[hand_point(*sample), 1] @ self.linear

    def mapping_scale(self, scale, depth_m=None):
        # Stereo depth replaces apparent size when both were recorded together.
        if self.depth_scale is not None and depth_m is not None and depth_m > 0:
            return self.depth_scale / depth_m
        return scale

    def locate(self, position, scale):
        """Return screen x, y and depth from close (0) to far (1)."""
        u, v, depth = self.raw((position[0], position[1], scale))
        levels = self.corrections
        if depth <= levels[0][0] or len(levels) == 1:
            screen = apply_homography(levels[0][1], (u, v))
        elif depth >= levels[-1][0]:
            screen = apply_homography(levels[-1][1], (u, v))
        else:
            for (low, low_map), (high, high_map) in zip(levels, levels[1:]):
                if low <= depth <= high:
                    t = (depth - low) / (high - low)
                    screen = (1 - t) * apply_homography(low_map, (u, v)) + t * apply_homography(high_map, (u, v))
                    break
        width = self.width
        return 0.5 + (float(screen[0]) - 0.5) / width, 0.5 + (float(screen[1]) - 0.5) / width, float(depth)

    def pointer(self, position, scale):
        return self.locate(position, scale)[:2]

    def outline(self, scale):
        """Image positions that reach the four screen corners at this apparent size."""
        corners = []
        for goal in CORNERS:
            guess = np.array((0.5, 0.5))
            for _ in range(8):
                value = np.array(self.pointer(guess, scale))
                jacobian = np.column_stack(
                    [(np.array(self.pointer(guess + step, scale)) - value) / 1e-4 for step in np.eye(2) * 1e-4]
                )
                try:
                    guess = guess + np.linalg.solve(jacobian, np.array(goal) - value)
                except np.linalg.LinAlgError:
                    return None
            if not np.isfinite(guess).all():
                return None
            corners.append(tuple(np.clip(guess, -0.5, 1.5)))
        return corners


ALIGN_SECONDS = 12.0
ALIGN_SPREAD = 0.045
ALIGN_DEPTH = 1.25
HOLD_SECONDS = 0.45
GLIDE_SECONDS = 0.6
# Distances in palm sizes, which don't change with distance from the camera. A camera
# turned up to about 45 degrees foreshortens sideways moves, so these stay modest.
TOGETHER = 1.0  # Hands count as together within about one palm size of each other.
SPREAD = 0.6
DROP = 0.5

# Faces of the box from your point of view: arms out toward the screen, close in, and halfway.
FACES = ("out", "in", "middle")
FACE_DEPTH = {"in": 0.0, "middle": 0.5, "out": 1.0}
FACE_TITLES = {"out": "Furthest out", "in": "Close in", "middle": "Middle"}
PHASES = ("top", "spread", "down", "bottom")
MARKS = {"top": (0.5, 0.0), "top_left": (0.0, 0.0), "top_right": (1.0, 0.0),
         "bottom_left": (0.0, 1.0), "bottom_right": (1.0, 1.0), "bottom": (0.5, 1.0)}
CORNER_MARKS = ("top_left", "top_right", "bottom_right", "bottom_left")
PHASE_TARGETS = {"top": ("top", "top"), "spread": ("top_left", "top_right"),
                 "down": ("bottom_left", "bottom_right"), "bottom": ("bottom", "bottom")}
SYNTHETIC_IN = 1 / 1.5

# Every step of a calibration, for the steps panel. A step is ("align", None), (face, phase) or ("done", None).
ALIGN_STEP = ("align", None)
DONE_STEP = ("done", None)
PHASE_TITLES = {"top": "Hands together at the top center", "spread": "Sweep apart to the top corners",
                "down": "Straight down to the bottom corners", "bottom": "Together at the bottom center"}
FACE_NOTES = {"out": "Furthest out · arms reaching toward the screen", "in": "Close in · hands pulled back toward you",
              "middle": "Middle · halfway between"}


def calibration_plan(stereo, faces=FACES):
    """The steps of one calibration, in order."""
    return ([ALIGN_STEP] if stereo else []) + [(face, phase) for face in faces for phase in PHASES]


def step_title(step):
    face, phase = step
    if face == "align":
        return "Turn both hands"
    if face == "done":
        return "Done · your box is saved"
    return PHASE_TITLES[phase]


def step_detail(step):
    face, phase = step
    if face == "align":
        return "Lines up the two cameras"
    if face == "done":
        return "Then gestures start"
    return FACE_NOTES[face] if phase == "top" else FACE_TITLES[face]


PATH_WEIGHT = 0.05
REFINE_DECAY = 0.7  # A refine pass counts fully. Earlier ones count 70% as much again, so the box follows small drift.
MAX_PASSES = 8
FACE_AT = {depth: face for face, depth in FACE_DEPTH.items()}
CORNER_NAMES = {"top_left": "Top left", "top_right": "Top right", "bottom_right": "Bottom right",
                "bottom_left": "Bottom left"}


def finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in values)


class DrawnPass:
    """One drawing of some sides of the box: each side's held marks and points along its strokes.

    A mark is (x, y, apparent palm size, stereo depth or None). A path point is
    (x, y, palm size, box x, box y, box depth, stereo depth or None).
    """

    def __init__(self, faces, paths=(), weight=1.0):
        def plain(values):
            return tuple(None if value is None else float(value) for value in values)
        self.faces = {face: {key: plain(mark) for key, mark in marks.items()} for face, marks in faces.items()}
        self.paths = [plain(point) for point in paths]
        self.weight = float(weight)

    def weighted(self, factor):
        return DrawnPass(self.faces, self.paths, self.weight * factor)

    def without(self, faces):
        """The same drawing minus some sides, or None if nothing is left."""
        kept = {face: marks for face, marks in self.faces.items() if face not in faces}
        depths = {FACE_DEPTH[face] for face in faces}
        if not kept:
            return None
        return DrawnPass(kept, [point for point in self.paths if point[5] not in depths], self.weight)

    def to_json(self):
        return {"faces": {face: {key: list(mark) for key, mark in marks.items()} for face, marks in self.faces.items()},
                "paths": [list(point) for point in self.paths], "weight": self.weight}

    @classmethod
    def from_json(cls, data):
        faces = {}
        for face, marks in data["faces"].items():
            if face not in FACE_DEPTH or set(marks) != set(MARKS):
                return None
            faces[face] = {}
            for key, (x, y, scale, depth) in marks.items():
                if not finite(x, y, scale) or scale <= 0 or (depth is not None and not (finite(depth) and depth > 0)):
                    return None
                faces[face][key] = (float(x), float(y), float(scale), None if depth is None else float(depth))
        paths = []
        for x, y, scale, u, v, w, depth in data.get("paths", []):
            if not finite(x, y, scale, u, v, w) or scale <= 0 or (depth is not None and not finite(depth)):
                return None
            paths.append((float(x), float(y), float(scale), float(u), float(v), float(w),
                          None if depth is None else float(depth)))
        weight = data.get("weight", 1.0)
        if not faces or not finite(weight) or weight <= 0:
            return None
        return cls(faces, paths, weight)


def build_reach(passes):
    """One box from every drawing: stereo depth when every mark has it, else apparent palm size."""
    passes = [p for p in passes if p is not None and p.faces]
    marks = [mark for p in passes for marks in p.faces.values() for mark in marks.values()]
    stereo = bool(marks) and all(mark[3] for mark in marks)
    depth_scale = float(median(mark[2] * mark[3] for mark in marks)) if stereo else None
    rows = []
    for drawing in passes:
        for face, face_marks in drawing.faces.items():
            depth = FACE_DEPTH[face]
            for key, (u, v) in MARKS.items():
                if key in face_marks:
                    x, y, scale, distance = face_marks[key]
                    corner = key in CORNER_MARKS
                    rows.append((x, y, depth_scale / distance if stereo else scale, u, v, depth,
                                 drawing.weight * (1.0 if corner else 0.6), "corner" if corner else "edge"))
        for x, y, scale, u, v, w, distance in drawing.paths:
            if stereo and not distance:
                continue
            rows.append((x, y, depth_scale / distance if stereo else scale, u, v, w,
                         drawing.weight * PATH_WEIGHT, "path"))
    reach = Reach(rows, depth_scale)
    reach.passes = passes
    return reach


def passes_from_rows(rows, depth_scale):
    """Rebuild the one drawing behind a box saved before drawings were kept, or None."""
    faces, paths = {}, []
    for x, y, scale, u, v, w, _weight, kind in rows:
        distance = depth_scale / scale if depth_scale else None
        if kind in ("corner", "edge"):
            face = FACE_AT.get(w)
            key = next((name for name, mark in MARKS.items() if mark == (u, v)), None)
            if face is None or key is None:
                return None
            faces.setdefault(face, {})[key] = (x, y, scale, distance)
        elif kind == "path":
            paths.append((x, y, scale, u, v, w, distance))
    if not faces or any(set(marks) != set(MARKS) for marks in faces.values()):
        return None
    return [DrawnPass(faces, paths)]


def corner_changes(old, new, faces):
    """How far each corner of these sides moved, as a share of the screen, judged by the old box."""
    changes = []
    width, old.width = old.width, 1.0
    try:
        for face in faces:
            for key in CORNER_MARKS:
                points, weights = [], []
                for drawing in new.passes or ():
                    mark = drawing.faces.get(face, {}).get(key)
                    if mark is not None:
                        x, y, scale, distance = mark
                        points.append(hand_point(x, y, old.depth_scale / distance
                                                 if old.depth_scale and distance else scale))
                        weights.append(drawing.weight)
                if points:
                    q = np.average(points, axis=0, weights=weights)
                    scale = 1 / q[2]
                    u, v, _ = old.locate((q[0] * scale, q[1] * scale), scale)
                    changes.append((face, key, float(np.hypot(u - MARKS[key][0], v - MARKS[key][1]))))
    finally:
        old.width = width
    return sorted(changes, key=lambda change: -change[2])


class BoxCalibration:
    """Help you draw a 3D control box in the air with both hands.

    With two cameras, you first turn both hands so the cameras can line up.
    Then you draw three faces of the box, starting furthest out: hands together
    at the top center, apart to the top corners, down to the bottom corners, and
    together again at the bottom center. The map is refined after every face,
    and each hand's marker is drawn through the current map as you move.
    """

    def __init__(self, stereo=False, align=None, faces=FACES, previous=None, mode="fresh"):
        """stereo says hands have stereo depth; align adds the camera-alignment step first (default: stereo).
        mode "refine" draws the whole box again and blends it into previous; "redo" redraws only the given
        faces and replaces them in previous. Both need a previous box that kept its drawings."""
        self.stereo = stereo
        self.align = stereo if align is None else align
        self.mode = mode if mode in ("refine", "redo") and previous is not None and previous.passes else "fresh"
        self.previous = previous if self.mode != "fresh" else None
        self.face_list = tuple(face for face in FACES if face in faces) if self.mode == "redo" else FACES
        if not self.face_list:
            self.face_list = FACES
        self.base_passes = []
        self.changes = []
        self.plan = calibration_plan(self.align, self.face_list)
        self.stage = "align" if self.align else "face"
        self.face_index = 0
        self.phase = 0
        self.faces = {}
        self.marks = {}
        self.paths = []
        self.path_depths = []
        self.path_frames = []
        self.hold = []
        self.stereo_misses = 0
        self.align_needed = ALIGN_SECONDS
        self.align_time = 0.0
        self.align_last = None
        self.align_scales = []
        self.align_turns = []
        self.align_retries = 0
        self.map = None
        if self.previous is not None:
            passes = self.previous.passes
            if self.mode == "refine":
                self.base_passes = [drawing.weighted(REFINE_DECAY) for drawing in passes][-(MAX_PASSES - 1):]
            else:
                self.base_passes = [kept for kept in (drawing.without(self.face_list) for drawing in passes) if kept]
            # Hands show inside your saved box from the start. A fresh copy, so its width is the drawn box.
            self.map = build_reach(passes)
        self.result = None
        self.hint = ""
        self.note = ""
        self.note_until = 0.0
        self.progress = 0.0
        self.hands = {"left": None, "right": None}
        self.trails = {"left": [], "right": []}
        self.targets = [(0.5, 0.0, 1.0), (0.5, 0.0, 1.0)]
        self.glides = None
        self.phase_started = None
        self.reset_targets(0.0, glide=False)

    # Progress and wording
    @property
    def face(self):
        return self.face_list[min(self.face_index, len(self.face_list) - 1)]

    @property
    def active_face(self):
        return self.face if self.stage == "face" else None

    @property
    def collecting_pair(self):
        """True while both cameras' views should feed the alignment fit."""
        return self.align and self.stage in ("align", "aligning")

    def step_index(self):
        """Where you are in self.plan."""
        if self.stage in ("align", "aligning"):
            return 0
        offset = 1 if self.plan[0] == ALIGN_STEP else 0
        index = min(self.face_index, len(self.face_list) - 1) * len(PHASES) + self.phase
        return min(offset + index, len(self.plan) - 1)

    def upcoming(self, count=3):
        """The next few steps after this one, ending with done."""
        return (self.plan[self.step_index() + 1:] + [DONE_STEP])[:count]

    def headline(self):
        if self.stage == "aligning":
            return "Lining up the cameras…"
        return step_title(self.plan[self.step_index()])

    def kicker(self):
        index = self.step_index()
        step = self.plan[index]
        where = "Both cameras" if step == ALIGN_STEP else FACE_TITLES[step[0]]
        kind = {"refine": "Refine · ", "redo": "Redo · "}.get(self.mode, "")
        return f"{kind}Step {index + 1} of {len(self.plan)} · {where}"

    def title(self):
        return f"{self.kicker()} · {self.headline()}"

    def instruction(self):
        if self.stage == "align":
            return ("Hold up both hands with your fingers spread. Slowly turn them and move them around a little, "
                    "up, down and side to side, as you bring them in close and back out.")
        if self.stage == "aligning":
            return "Keep your hands up for a moment."
        face, phase = self.face, PHASES[self.phase]
        if phase == "top":
            text = {
                "out": "Reach out toward the screen, as far as is comfortable. Put your hands together at the top "
                       "center of your box.",
                "in": "pull your hands back in toward you. Put them together at the top center.",
                "middle": "move halfway between close in and arms out. Hands together at the top center.",
            }[face]
            text = ("Now " + text) if self.face_index and face != "out" else text
            return text[:1].upper() + text[1:]
        return {
            "spread": "Sweep your hands apart to the top corners, then hold.",
            "down": "Bring both hands straight down to the bottom corners, then hold.",
            "bottom": "Bring your hands together at the bottom center, then hold.",
        }[phase]

    def set_note(self, note, now, seconds=2.5):
        self.note, self.note_until = note, now + seconds

    # Targets glide from where the last step ended.
    def phase_targets(self):
        depth = FACE_DEPTH[self.face]
        return [(*MARKS[key], depth) for key in PHASE_TARGETS[PHASES[self.phase]]]

    def reset_targets(self, now, glide=True):
        self.phase_started = now if glide else None
        goals = self.phase_targets()
        self.glides = [(tuple(source), goal, now) for source, goal in zip(self.targets, goals)] if glide else None
        if not glide:
            self.targets = goals

    def update_targets(self, now):
        if self.glides is None:
            return
        t = clipped((now - self.glides[0][2]) / GLIDE_SECONDS)
        t = t * t * (3 - 2 * t)
        self.targets = [tuple(a + (b - a) * t for a, b in zip(source, goal)) for source, goal, _ in self.glides]
        if t >= 1:
            self.glides = None

    # What the drawing view shows
    def segments(self):
        """Edges already drawn, in box coordinates."""
        lines = []
        done = [face for face in FACES if face in self.faces]
        for face in done:
            depth = FACE_DEPTH[face]
            corners = [(*MARKS[key], depth) for key in CORNER_MARKS]
            lines += list(zip(corners, corners[1:] + corners[:1]))
        for a, b in zip(done, done[1:]):
            lines += [((*MARKS[key], FACE_DEPTH[a]), (*MARKS[key], FACE_DEPTH[b])) for key in CORNER_MARKS]
        if self.stage == "face":
            depth = FACE_DEPTH[self.face]
            for start, end in (("top", "top_left"), ("top", "top_right"), ("top_left", "bottom_left"),
                               ("top_right", "bottom_right"), ("bottom_left", "bottom"), ("bottom_right", "bottom")):
                if start in self.marks and end in self.marks:
                    lines.append(((*MARKS[start], depth), (*MARKS[end], depth)))
        return lines

    def clear_hands(self):
        self.hands = {"left": None, "right": None}

    # Map construction
    @staticmethod
    def scale_face(marks, ratio):
        """The same face moved along the camera's line of sight, for a rough first map."""
        return {key: (0.5 + (x - 0.5) * ratio, 0.5 + (y - 0.5) * ratio, scale * ratio, None)
                for key, (x, y, scale, _) in marks.items()}

    @staticmethod
    def middle_face(a, b):
        """Halfway between two faces in camera space."""
        marks = {}
        for key in a:
            q = (hand_point(*a[key][:3]) + hand_point(*b[key][:3])) / 2
            scale = 1 / q[2]
            marks[key] = (q[0] * scale, q[1] * scale, scale, None)
        return marks

    def face_rows(self, face, marks, weight=1.0):
        depth = FACE_DEPTH[face]
        rows = []
        for key, (u, v) in MARKS.items():
            x, y, scale, _ = marks[key]
            corner = key in CORNER_MARKS
            rows.append((x, y, scale, u, v, depth, weight if corner else 0.6 * weight, "corner" if corner else "edge"))
        return rows

    def samples(self):
        """Rows for a rough map while a fresh box is still being drawn, filling in sides not drawn yet."""
        rows = []
        for face, marks in self.faces.items():
            rows += self.face_rows(face, marks)
        rows += self.paths
        faces = dict(self.faces)
        if "out" in faces and "in" not in faces:
            faces["in"] = self.scale_face(faces["out"], SYNTHETIC_IN)
            rows += self.face_rows("in", faces["in"], 0.6)
        if "out" in faces and "middle" not in faces:
            rows += self.face_rows("middle", self.middle_face(faces["out"], faces["in"]), 0.6)
        return rows

    def current_pass(self):
        """What you've drawn so far in this session, as one drawing."""
        paths = [(x, y, scale, u, v, w, distance)
                 for (x, y, scale, u, v, w, _, _), distance in zip(self.paths, self.path_depths)]
        return DrawnPass(self.faces, paths)

    def refine(self):
        if self.base_passes:
            # Refining or redoing a side: everything else comes from the saved drawings.
            try:
                self.map = build_reach(self.base_passes + [self.current_pass()])
            except (ValueError, np.linalg.LinAlgError):
                pass  # Keep the previous map.
            return
        if "out" not in self.faces:
            return
        try:
            self.map = Reach(self.samples())
        except (ValueError, np.linalg.LinAlgError):
            pass  # Keep the previous map until the next face adds more.

    def provisional(self, x, y, scale):
        """Where a hand sits in the box before the first face exists."""
        marks = self.marks
        top = marks.get("top")
        cx, cy = (top[0], top[1]) if top else (0.5, 0.35)
        half = 1.5 * (top[2] if top else scale)
        if "top_left" in marks:
            left, right = marks["top_left"], marks["top_right"]
            cx, half = (left[0] + right[0]) / 2, max(0.02, (right[0] - left[0]) / 2)
            cy = (left[1] + right[1]) / 2
        height = 1.2 * half
        if "bottom_left" in marks:
            height = max(0.02, (marks["bottom_left"][1] + marks["bottom_right"][1]) / 2 - cy)
        depth = 1.0 if top is None else 1.0 - clipped((1 - scale / top[2]) / 0.4)
        return 0.5 + (x - cx) / (2 * half), (y - cy) / height, depth

    def locate(self, hand):
        x, y, scale, _ = hand
        if self.map is not None:
            return self.map.locate((x, y), scale)
        return self.provisional(x, y, scale)

    # Live input
    def observe(self, hands, now, features=None):
        """Feed one frame. hands holds (x, y, apparent palm size, stereo depth or None) per visible hand."""
        if self.result is not None:
            return
        self.update_targets(now)
        hands = [h for h in hands if h is not None and h[2] and isfinite(h[2]) and h[2] > 0
                 and 0 <= h[0] <= 1 and 0 <= h[1] <= 1]
        hands.sort(key=lambda h: h[0])
        sides = {}
        if len(hands) == 2:
            sides = {"left": hands[0], "right": hands[1]}
        elif len(hands) == 1:
            # One hand: show it on whichever side it was last seen.
            last = {side: value for side, value in self.hands.items() if value is not None}
            side = "left"
            if last:
                located = self.locate(hands[0])
                side = min(last, key=lambda name: abs(last[name][0] - located[0]))
            sides = {side: hands[0]}
        for side in ("left", "right"):
            hand = sides.get(side)
            if hand is None:
                self.hands[side] = None
                continue
            located = tuple(float(np.clip(value, -0.2, 1.2)) for value in self.locate(hand))
            previous = self.hands[side]
            # Smooth each marker so it glides with your hand rather than jumping.
            self.hands[side] = located if previous is None else tuple(p + 0.4 * (n - p) for p, n in zip(previous, located))
            if self.stage == "face" and PHASES[self.phase] != "top":
                trail = self.trails[side]
                if not trail or np.linalg.norm(np.subtract(trail[-1], self.hands[side])) > 0.01:
                    trail.append(self.hands[side])
                    del trail[:-60]
        if self.stage == "align":
            self.observe_align(hands, now, features)
        elif self.stage == "aligning":
            self.hint = "Lining up the cameras…"
        elif len(hands) < 2:
            self.hold.clear()
            self.hint = ("The camera sees one hand. Raise the other one too." if hands
                         else "Raise both hands where the camera can see them.")
        else:
            self.observe_face(sides["left"], sides["right"], now)
        if self.note and now < self.note_until:
            self.hint = f"{self.note} {self.hint}"

    def observe_align(self, hands, now, features):
        if len(hands) < 2:
            self.align_last = None
            self.hint = "Raise both hands where both cameras can see them."
            return
        if features is None:
            self.align_last = None
            self.hint = "Spread your fingers so the cameras see all five fingertips."
            return
        if self.align_last is not None:
            self.align_time += min(0.25, now - self.align_last)
        self.align_last = now
        self.align_scales.append(np.mean([h[2] for h in hands]))
        self.align_turns.append(np.asarray(features)[3:])
        scales = np.array(self.align_scales)
        depth = float(np.percentile(scales, 85) / np.percentile(scales, 15)) if len(scales) >= 10 else 1.0
        turns = np.array(self.align_turns)
        spread = float(np.percentile(np.linalg.norm(turns - np.median(turns, axis=0), axis=1), 85)) if len(turns) >= 5 else 0.0
        self.progress = (0.6 * min(1.0, self.align_time / self.align_needed)
                         + 0.2 * min(1.0, (depth - 1) / (ALIGN_DEPTH - 1)) + 0.2 * min(1.0, spread / ALIGN_SPREAD))
        if depth < ALIGN_DEPTH:
            self.hint = "Bring your hands in close, then back out."
        elif spread < ALIGN_SPREAD:
            self.hint = "Turn your hands more: left, right, and tilt them."
        elif self.align_time < self.align_needed:
            self.hint = "Keep turning slowly."
        else:
            self.stage = "aligning"
            self.progress = 1.0
            self.hint = "Lining up the cameras…"

    def alignment_done(self, ok, now, message=""):
        """The app reports whether the turning lined up the two cameras."""
        if self.stage != "aligning":
            return
        if ok:
            self.start_faces(now, "Cameras lined up.")
        elif self.align_retries < 2:
            self.align_retries += 1
            self.align_needed = self.align_time + 6.0
            self.stage = "align"
            self.align_last = None
            self.set_note(f"{message} Keep turning a little longer.", now, 4.0)
        else:
            self.stereo = False
            self.start_faces(now, "The second camera couldn't be lined up. Continuing with one camera.")

    def start_faces(self, now, note=""):
        self.stage = "face"
        self.progress = 0.0
        if note:
            self.set_note(note, now)
        self.reset_targets(now)

    def depth_problem(self, left, right):
        """A hint when the hands aren't at this face's distance, or None."""
        face, phase = self.face, PHASES[self.phase]
        palm = (left[2] + right[2]) / 2
        # Side by side, both hands are about as far away. Spread apart, a turned camera
        # really does see one hand closer than the other, so each is checked on its own.
        if phase in ("top", "bottom") and not 0.67 <= left[2] / right[2] <= 1.5:
            return "Hold both hands at the same distance from the camera."
        top = self.marks.get("top")
        if top is not None and phase != "top" and not all(0.5 <= hand[2] / top[2] <= 2.0 for hand in (left, right)):
            return "Keep your hands at this distance."
        if phase != "top":
            return None
        # Depth through the map, which already allows for a tilted camera. Raw hand size
        # can't tell "pulled back" from "lower down" when the camera looks down at you.
        depth = None
        if self.map is not None:
            depth = np.mean([self.map.locate(hand[:2], hand[2])[2] for hand in (left, right)])
        if face == "in":
            if depth is not None and depth > 0.6:
                return "Pull your hands back in toward you."
            if depth is None and "out" in self.faces and palm > 0.85 * self.faces["out"]["top"][2]:
                return "Pull your hands back in toward you."
        if face == "middle" and depth is not None:
            if depth < 0.2:
                return "Move your hands a little farther out."
            if depth > 0.8:
                return "Pull your hands back a little."
        return None

    def shape_problem(self, left, right):
        """A hint when the hands aren't where this part of the outline goes, or None."""
        phase = PHASES[self.phase]
        palm = (left[2] + right[2]) / 2
        apart = np.hypot(left[0] - right[0], left[1] - right[1])
        marks = self.marks
        if phase == "top":
            return None if apart <= TOGETHER * palm else "Bring your hands together."
        top = marks["top"]
        if phase == "spread":
            if (top[0] - left[0]) / left[2] < SPREAD or (right[0] - top[0]) / right[2] < SPREAD:
                return "Sweep your hands farther apart."
            if max(abs(left[1] - top[1]) / left[2], abs(right[1] - top[1]) / right[2]) > 2.0:
                return "Keep your hands level with where they started."
            return None
        if phase == "down":
            corners = marks["top_left"], marks["top_right"]
            if any((hand[1] - corner[1]) / hand[2] < DROP for hand, corner in zip((left, right), corners)):
                return "Bring your hands farther down."
            if any(abs(hand[0] - corner[0]) / hand[2] > 1.6 for hand, corner in zip((left, right), corners)):
                return "Move straight down from the top corners."
            return None
        if apart > TOGETHER * palm:
            return "Bring your hands together."
        if (left[1] + right[1]) / 2 - top[1] < DROP * palm:
            return "Bring your hands together lower down, at the bottom center."
        return None

    def steady(self, left, right, now):
        if self.hold:
            _, first_left, first_right = self.hold[0]
            gap = now - self.hold[-1][0]
            # A hand near the camera covers more of the picture, so the same tremor moves it farther.
            moved = any(abs(a[0] - b[0]) > limit or abs(a[1] - b[1]) > limit or abs(a[2] / b[2] - 1) > 0.12
                        for a, b in ((left, first_left), (right, first_right))
                        for limit in (max(0.028, 0.15 * min(a[2], b[2])),))
            if not 0 < gap <= 0.2 or moved:
                self.hold.clear()
        self.hold.append((now, left, right))
        elapsed = self.hold[-1][0] - self.hold[0][0]
        self.progress = min(1.0, elapsed / HOLD_SECONDS)
        if elapsed < HOLD_SECONDS or len(self.hold) < 5:
            return None
        held = []
        settled = self.hold[len(self.hold) // 3:]  # The first moments of a hold can still be settling.
        for index in (1, 2):
            frames = [frame[index] for frame in settled]
            depths = [frame[3] for frame in frames if frame[3]]
            held.append((median(f[0] for f in frames), median(f[1] for f in frames), median(f[2] for f in frames),
                         median(depths) if len(depths) > len(frames) / 2 else None))
        self.hold = []
        return held

    def add_path(self, side, start, end, depth):
        frames = [frame[side] for frame in self.path_frames]
        if len(frames) < 6:
            return
        points = np.array([hand_point(*f[:3]) for f in frames])
        steps = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
        if steps[-1] <= 0:
            return
        start, end = np.array((*MARKS[start], depth)), np.array((*MARKS[end], depth))
        for index in np.linspace(1, len(frames) - 2, min(10, len(frames) - 2)).round().astype(int):
            target = start + steps[index] / steps[-1] * (end - start)
            x, y, scale, hand_depth = frames[index]
            self.paths.append((x, y, scale, *target, PATH_WEIGHT, "path"))
            self.path_depths.append(hand_depth)

    def observe_face(self, left, right, now):
        phase = PHASES[self.phase]
        if phase != "top":
            self.path_frames.append((left, right))
        problem = self.depth_problem(left, right) or self.shape_problem(left, right)
        if problem:
            self.hold.clear()
            self.progress = 0.0
            self.hint = problem
            return
        # Give a moment to read each new step before a hold counts, so hands that happen
        # to be together when a side starts don't place its top right away.
        if self.phase_started is None:
            self.phase_started = now
        settle = (1.0 if self.phase == 0 else 0.25) - (now - self.phase_started)
        if settle > 0:
            self.hold.clear()
            self.progress = 0.0
            return
        self.hint = "Hold still while the rings fill."
        held = self.steady(left, right, now)
        if held is None:
            return
        if self.stereo and not (held[0][3] and held[1][3]):
            # Give the second camera a moment, then carry on with one camera for this spot.
            self.stereo_misses += 1
            if self.stereo_misses < 4:
                self.hint = "Keep both hands where both cameras can see them."
                return
        self.stereo_misses = 0
        depth = FACE_DEPTH[self.face]
        keys = PHASE_TARGETS[phase]
        if phase == "top":
            self.marks["top"] = self.together(held)
        elif phase == "bottom":
            self.add_path(0, "bottom_left", "bottom", depth)
            self.add_path(1, "bottom_right", "bottom", depth)
            self.marks["bottom"] = self.together(held)
        else:
            starts = ("top", "top") if phase == "spread" else ("top_left", "top_right")
            for side, (start, end) in enumerate(zip(starts, keys)):
                self.add_path(side, start, end, depth)
                self.marks[end] = held[side]
        self.path_frames = []
        self.trails = {"left": [], "right": []}
        if phase == "bottom":
            self.finish_face(now)
        else:
            self.phase += 1
            self.progress = 0.0
            self.reset_targets(now)

    @staticmethod
    def together(held):
        (lx, ly, ls, ld), (rx, ry, rs, rd) = held
        depth = (ld + rd) / 2 if ld and rd else None
        return (lx + rx) / 2, (ly + ry) / 2, (ls + rs) / 2, depth

    def finish_face(self, now):
        marks = self.marks
        corners = np.array([marks[key][:2] for key in CORNER_MARKS])
        palm = np.mean([marks[key][2] for key in CORNER_MARKS])
        width = np.mean((marks["top_right"][0] - marks["top_left"][0], marks["bottom_right"][0] - marks["bottom_left"][0]))
        height = np.mean((marks["bottom_left"][1] - marks["top_left"][1], marks["bottom_right"][1] - marks["top_right"][1]))
        if not convex(corners) or width < 1.2 * palm or height < 0.6 * palm:
            self.marks, self.phase = {}, 0
            self.set_note("That side came out too small or crossed over. Draw it again, a little bigger.", now, 4.0)
            self.reset_targets(now)
            return
        self.faces[self.face] = marks
        self.marks = {}
        self.refine()
        self.face_index += 1
        self.phase = 0
        self.progress = 0.0
        if self.face_index >= len(self.face_list):
            self.finish()
            return
        self.set_note("Map refined.", now)
        self.reset_targets(now)

    def finish(self):
        try:
            self.result = build_reach(self.base_passes + [self.current_pass()])
            if self.previous is not None:
                self.changes = corner_changes(self.previous, self.result, self.face_list)
        except (ValueError, np.linalg.LinAlgError) as exc:
            self.result = None
            stereo, previous, mode, faces = self.stereo, self.previous, self.mode, self.face_list
            self.__init__(stereo, align=False, faces=faces, previous=previous, mode=mode)
            self.hint = f"{exc} Let's draw it again."


def profile_path(camera_index, aspect):
    if sys.platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "MimeControl" / f"reach-{camera_index}-{aspect}.json"


def load_reach(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        version = data["version"]
        if version in (3, 4):
            reach = Reach(data["samples"], data.get("depth_scale"))
            passes = None
            if version == 4 and isinstance(data.get("passes"), list) and data["passes"]:
                passes = [DrawnPass.from_json(item) for item in data["passes"]]
                if any(item is None for item in passes):
                    passes = None
            reach.passes = passes or passes_from_rows(reach.samples, reach.depth_scale)
            return reach
        # Older saves kept only a close and a far box: convert their corners.
        if version == 2:
            targets, depth_scale = data["targets"], data.get("depth_scale")
            faces = ([t for i, t in enumerate(targets[:5]) if i], [t for i, t in enumerate(targets[5:]) if i])
        elif version == 1:
            faces, products = [], []
            for face, depth in zip((data["near"], data["far"]), data.get("depths") or (None, None)):
                left, top, right, bottom, scale = (face[k] for k in ("left", "top", "right", "bottom", "scale"))
                faces.append([(left + x * (right - left), top + y * (bottom - top), scale) for x, y in CORNERS])
                if depth:
                    products.append(scale * depth)
            depth_scale = median(products) if len(products) == 2 else None
        else:
            return None
        samples = [(*target, *corner, depth, 1.0, "corner")
                   for face, depth in zip(faces, (0.0, 1.0)) for target, corner in zip(face, CORNERS)]
        reach = Reach(samples, depth_scale)
        reach.passes = passes_from_rows(reach.samples, reach.depth_scale)
        return reach
    except (KeyError, OSError, OverflowError, ValueError, TypeError, RecursionError, AttributeError,
            np.linalg.LinAlgError):
        pass
    return None


def save_reach(path, reach):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as out:
            temp = Path(out.name)
            data = {"version": 4, "samples": reach.samples, "depth_scale": reach.depth_scale}
            if reach.passes:
                data["passes"] = [drawing.to_json() for drawing in reach.passes]
            json.dump(data, out, allow_nan=False)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
