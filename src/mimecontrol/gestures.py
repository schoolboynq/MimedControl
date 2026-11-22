from collections import deque
from dataclasses import dataclass, replace
from math import copysign, dist, hypot, log

import numpy as np

from .air_tap import AirTap
from .aim import PointerFilter, weight
from .calibration import palm_scale
from .drag import LOST_GRACE, DragTracker
from .relaxed_hand import RELAXED_HAND, RelaxedClicks, relaxed_shape
from .swipe import ScreenSwipe

PAD_KEYS = (
    ("7", "8", "9", "Backspace"),
    ("4", "5", "6", "-"),
    ("1", "2", "3", "+"),
    ("0", "0", ".", "Enter"),
)
ACTIVE_AREA = (0.16, 0.14, 0.84, 0.84)
PALM_ACTIVE_AREA = (0.24, 0.23, 0.76, 0.77)
FULL_FRAME = (0.0, 0.0, 1.0, 1.0)
DEFAULT_PALM_TRAVEL = "Full frame"
PALM_AREAS = {
    DEFAULT_PALM_TRAVEL: FULL_FRAME,
    "Small": (0.30, 0.30, 0.70, 0.70),
    "Medium": PALM_ACTIVE_AREA,
    "Large": ACTIVE_AREA,
}
PALM_MODES = (RELAXED_HAND, "Palm + pinch")
CLICK_GESTURES = (*PALM_MODES, "Pinch focus", "Point + pinch", "Air tap")
PINCH_CONTACT = 0.25
PINCH_CONTACT_ASSIST = 0.34
PINCH_PAD_OPENING = 0.48
PINCH_PAD_SQUEEZE = 0.16
PINCH_PAD_WINDOW = 0.5
PINCH_READY_MIN = 0.32
PINCH_READY_MAX = 0.90
PINCH_HOLD = 0.29
SMALL_PALM_SPAN = 0.07
PALM_SCALE_AGREEMENT = 0.85
PALM_SCALE_SAMPLES = 3
SMALL_MOVING_PALM_SPAN = 0.09
PALM_MOTION_SETTLE_TIME = 0.24
RIGHT_OPEN = 0.40
RIGHT_INDEX_CLEAR = 0.36
RIGHT_SQUEEZE = 0.14
RIGHT_CLOSING = 0.38
PALM_STARTS = [0, 0, 5]
PALM_ENDS = [5, 17, 17]
# While Relaxed hand holds the button, one of index or middle within this of the thumb keeps it held.
RELAXED_HOLD = 0.45
RELAXED_HOLD_MAX = 0.60
FINGER_BASES = (5, 9, 13, 17)


def aim_area(mode, palm_travel, *, number_pad=False):
    if mode not in PALM_MODES:
        return ACTIVE_AREA if number_pad else FULL_FRAME
    if number_pad and palm_travel == DEFAULT_PALM_TRAVEL:
        return PALM_ACTIVE_AREA
    return PALM_AREAS[palm_travel]


def pad_key(pointer):
    if pointer is None:
        return None
    x, y = pointer
    return PAD_KEYS[min(int(y * 4), 3)][min(int(x * 4), 3)]


def extended(points, root, alignment=0.62):
    base = points[root + 1] - points[root]
    end = points[root + 3] - points[root + 1]
    lengths = np.linalg.norm(base) * np.linalg.norm(end)
    return lengths > 0 and np.dot(base, end) > alignment * lengths


def finger_pose(points, world, alignment=0.62):
    # World landmarks retain the bend of a finger as it points toward or away from the camera.
    joints = points if world is None else world
    return tuple(extended(joints, root, alignment) for root in FINGER_BASES)


def hand_size(points):
    return max(dist(points[0], points[9]), dist(points[5], points[17]))


def pinch_separation(points, world, image_to_world=None, tip=8):
    if world is not None:
        size = hand_size(world)
        if size > 0.001:
            if image_to_world is None:
                lateral = dist(world[4, :2], world[tip, :2])
            else:
                # Separate visible fingertip spacing from depth, then compare both in the same units.
                lateral = dist(points[4, :2], points[tip, :2]) * image_to_world
            return lateral / size, abs(float(world[4, 2] - world[tip, 2])) / size
    size = max(hand_size(points), 1)
    return dist(points[4, :2], points[tip, :2]) / size, abs(float(points[4, 2] - points[tip, 2])) / size


def pinch_distance(points, world, image_to_world=None, tip=8):
    return hypot(*pinch_separation(points, world, image_to_world, tip))


def palm_span(points):
    return float(np.linalg.norm(points[PALM_STARTS, :2] - points[PALM_ENDS, :2]))


def relative_depth(points, world, width, image_span=None):
    if world is not None:
        if image_span is None:
            image_span = palm_span(points)
        world_span = np.linalg.norm(world[PALM_STARTS, :2] - world[PALM_ENDS, :2])
        if image_span > 6 and world_span > 0.001:
            # Image projection scales inversely with distance. Model XY spans account for palm tilt.
            return float(width * world_span / image_span)
    return None


def palm_center(points):
    return (points[0, :2] + points[9, :2]) * 0.5


def palm_knuckles(points):
    return points[FINGER_BASES, :2].mean(axis=0)


def aim_landmark(points, mode):
    if mode in PALM_MODES:
        return palm_knuckles(points)
    if mode == "Pinch focus":
        return (points[4, :2] + points[8, :2]) * 0.5
    return points[8, :2]


def pinch_shape(points, tip=8):
    base = tip - 3
    finger = points[tip] - points[base]
    reach_squared = float(finger @ finger)
    if reach_squared <= 0:
        return 0.0, 0.0
    reach = reach_squared ** 0.5 / max(hand_size(points), 0.001)
    thumb_advance = float((points[4] - points[base]) @ finger) / reach_squared
    return reach, thumb_advance


def bound_screen(value):
    return min(1.0, max(0.0, value))


def observed_squeeze(history, gap, now):
    """Use two successive inward movements to confirm a quick pinch on its first contact frame."""
    closing = False
    if len(history) == 2:
        (start, start_time), (middle, middle_time) = history
        closing = (start_time < middle_time < now <= start_time + 0.15
                   and start >= PINCH_PAD_OPENING and middle > PINCH_CONTACT_ASSIST
                   and start - middle >= 0.055 and middle - gap >= 0.055
                   and start - gap >= PINCH_PAD_SQUEEZE)
    history.append((gap, now))
    return closing


@dataclass(frozen=True)
class Gesture:
    pose: str
    pointer: tuple[float, float] | None = None
    click: bool = False
    steps: int = 0
    toggle_pad: bool = False
    aim_held: bool = False
    right_click: bool = False
    camera_pointer: tuple[float, float] | None = None
    # Where the hand aims before clamping to the screen, so reaching past the box edge shows.
    raw_pointer: tuple[float, float] | None = None
    screen_swipe: tuple[int, int] | None = None
    # Press and hold: the button goes down (press) and comes up (release). While it is held, drag_motion
    # is the hand's travel since the drag began, or None while the pointer stays where it went down.
    press: bool = False
    release: bool = False
    held: bool = False
    drag_motion: tuple[float, float] | None = None
    drag_hand: tuple[float, float] | None = None


class GestureState:
    def __init__(self, click_gesture=RELAXED_HAND):
        self.aim = PointerFilter()
        self.pad_aim = PointerFilter()
        self.palm_aim = PointerFilter(elastic=True)
        self.pinch_aim = PointerFilter(elastic=True)
        self.pinch_pointer = None
        self.cursor = self.aim
        self.response = "Balanced"
        self.palm_travel = DEFAULT_PALM_TRAVEL
        self.reach = None
        self.click_gesture = click_gesture
        self.relaxed_clicks = RelaxedClicks()
        self.air_tap = AirTap()
        self.left_gaps = deque(maxlen=2)
        self.right_gaps = deque(maxlen=2)
        self.swipe = ScreenSwipe()
        self.swipe_enabled = False
        self.camera_aim = False
        self.drag = DragTracker()
        self.drag_enabled = True
        self.reset()

    def reset(self, keep_palm=False, keep_pointer=False):
        self.relaxed_clicks.reset()
        self.drag.reset()
        self.reach_scale = self.reach_time = None
        if not keep_pointer:
            self.size_ratio = None
        self.air_tap.reset()
        self.swipe.interrupt()
        self.camera_reseed = True
        self.camera_pointer = None
        if keep_pointer:
            self.aim.hold()
            self.pad_aim.hold()
            self.palm_aim.hold()
        else:
            self.aim.reset()
            self.pad_aim.reset()
            self.palm_aim.reset()
            self.pinch_pointer = None
            self.reference_depth = self.depth_time = None
            self.depth_ratio = 1.0
        self.pose = "idle"
        self.candidate = "idle"
        self.frames = 0
        self.previous_y = None
        self.travel = 0.0
        self.pinch_span_range = None
        self.pinch_scale_samples = 0
        self.pinch_scale_ready = False
        self.last_palm_motion = None
        self.palm_motion_until = 0.0
        self.palm_quiet_frames = 0
        self.release_pinch()
        self.release_right_pinch()
        self.aim_paused = False
        if not keep_palm:
            self.palm_since = None
            self.palm_anchor = None
            self.palm_used = False

    def recenter_aim(self):
        for aim in (self.aim, self.palm_aim, self.pinch_aim):
            aim.reset()
        self.pinch_pointer = self.pinch_sample = None
        self.aim_paused = False
        self.camera_reseed = True
        self.camera_pointer = None

    def confirm_screen_swipe(self):
        self.swipe.confirm()
        self.relaxed_clicks.reset()
        self.recenter_aim()
        self.air_tap.reset()
        self.release_pinch()
        self.release_right_pinch()

    def release_pinch(self):
        self.left_gaps.clear()
        self.armed = self.pinched = False
        self.contact_frames = self.reopen_frames = 0
        self.pinch_closed_gap = 0.0
        self.previous_gap = self.pinch_sample = self.pinch_opening = None

    def release_right_pinch(self):
        self.right_gaps.clear()
        self.right_opening = None
        self.right_held = False
        self.right_contact_frames = 0

    def update(self, points, width, height, now, number_pad=False, *, world=None, second=None, second_world=None,
               desktop_pointer=None, camera_aim=False, depth_m=None):
        self.depth_m = depth_m
        self.camera_aim = camera_aim and not number_pad
        self.screen_swipe = None
        gesture = self.observe(points, width, height, now, number_pad, world, second, second_world, desktop_pointer)
        if gesture.camera_pointer is None and not (self.click_gesture == RELAXED_HAND and gesture.aim_held):
            self.camera_reseed = True
        if self.screen_swipe is not None and gesture.pose == "point" and not gesture.aim_held:
            return replace(gesture, screen_swipe=self.screen_swipe)
        return gesture

    def observe(self, points, width, height, now, number_pad, world, second, second_world, desktop_pointer):
        palm_mode = self.click_gesture == "Palm + pinch"
        relaxed_mode = self.click_gesture == RELAXED_HAND
        focus_mode = self.click_gesture == "Pinch focus"
        self.cursor = self.palm_aim if self.click_gesture in PALM_MODES else self.aim
        if number_pad:
            self.cursor = self.pad_aim
        elif self.camera_aim and focus_mode:
            self.cursor = self.pinch_aim
        if points is None:
            if self.drag.active:
                if now - self.drag.seen_at <= LOST_GRACE:
                    return Gesture("point", None, aim_held=True, held=True)
                return self.end_drag(now, lost=True)
            self.reset(keep_pointer=True)
            return Gesture("lost")

        span = palm_span(points)
        if self.camera_aim and self.reach is not None:
            size = palm_scale(points, width)
            scale = self.reach.mapping_scale(size, self.depth_m)
            # Frames without a stereo match fall back to apparent size. Rescale it by the
            # recent stereo-to-size ratio so the pointer doesn't jump between the two.
            if scale != size:
                ratio = scale / size
                self.size_ratio = ratio if self.size_ratio is None else self.size_ratio + 0.2 * (ratio - self.size_ratio)
            elif self.size_ratio is not None and self.reach.depth_scale is not None:
                scale = size * self.size_ratio
            if self.reach_time is None or not 0 < now - self.reach_time < .2:
                self.reach_scale = scale
            else:
                self.reach_scale += weight(5, now - self.reach_time) * (scale - self.reach_scale)
            self.reach_time = now
        normalized_span = span / width
        self.update_pinch_scale(normalized_span)
        knuckles = palm_knuckles(points)
        palm_point = tuple(knuckles / width)
        unstable_motion = self.unstable_palm_motion(palm_point, normalized_span, now)
        depth = relative_depth(points, world, width, span)
        self.update_depth(depth, now)
        image_to_world = depth / width if depth is not None else None
        lateral_gap, depth_gap = pinch_separation(points, world, image_to_world)
        pinch_gap = hypot(lateral_gap, depth_gap)
        fingers = finger_pose(points, world, alignment=0.82 if palm_mode else 0.62)
        joints = points if world is None else world
        pinch_mode = self.click_gesture not in ("Air tap", RELAXED_HAND)
        assisted_contact = (pinch_mode and PINCH_CONTACT < pinch_gap <= PINCH_CONTACT_ASSIST
                            and (pinch_distance(points, None) <= 0.18
                                 or self.pad_contact(lateral_gap, depth_gap, pinch_gap, now)))
        closing = self.previous_gap is not None and self.previous_gap - pinch_gap > 0.09 and pinch_gap < 0.55
        pointing = fingers == (True, False, False, False)
        open_palm = all(fingers) and pinch_gap > 0.65
        if self.click_gesture in PALM_MODES and open_palm:
            open_palm = dist(joints[8], joints[20]) >= 0.65 * hand_size(joints)
        shape = None
        if relaxed_mode:
            middle_gap = pinch_distance(points, world, image_to_world, tip=12)
            shape = relaxed_shape(joints, fingers, pinch_gap, middle_gap, open_palm)
            open_palm = shape == "palm"
        second_open = (not number_pad and second is not None
                       and all(finger_pose(second, second_world))
                       and pinch_distance(second, second_world) > 0.65)
        if self.drag.active:
            return self.follow_drag(points, knuckles, width, height, now, number_pad, pinch_gap,
                                    middle_gap if relaxed_mode else None, assisted_contact, second_open)
        if open_palm and not second_open:
            if self.click_gesture in PALM_MODES:
                # An open hand can aim. Toggle the pad only after a deliberate stationary hold.
                palm_position = self.position(knuckles, width, height, ratio=1.0)
                if self.palm_anchor is None or dist(palm_position, self.palm_anchor) > 0.035:
                    self.palm_since = now
                    self.palm_anchor = palm_position
                if not self.palm_used and now - self.palm_since >= 0.85:
                    self.palm_used = True
                    self.reset(keep_palm=True, keep_pointer=True)
                    return Gesture("palm", toggle_pad=True)
            else:
                if self.palm_since is None:
                    self.palm_since = now
                self.reset(keep_palm=True, keep_pointer=True)
                toggle = not self.palm_used and now - self.palm_since >= 0.85
                self.palm_used |= toggle
                return Gesture("palm", toggle_pad=toggle)
        else:
            self.palm_since = self.palm_anchor = None
        # A momentary change in finger estimates shouldn't toggle the pad twice.
        if not all(fingers):
            self.palm_used = False

        # An already-aimed pinch can briefly look like a two-finger pose as tips overlap.
        keep_grip = (pinch_mode and self.pose == "point" and not second_open and (palm_mode or sum(fingers[1:]) <= 1)
                     and (self.armed or self.pinched)
                     and (pinch_gap <= PINCH_CONTACT or assisted_contact
                          or (pinch_gap <= 0.37 and (closing or self.pinched))))
        if second_open and open_palm:
            candidate = "two_zoom"
        elif second_open and (shape == "ready" if relaxed_mode else pointing or palm_mode):
            candidate = "two_scroll"
        elif relaxed_mode:
            if shape in ("scroll", "zoom") and not number_pad:
                candidate = shape
            elif shape == "ready" or (self.relaxed_clicks.active and shape in ("other", "left", "right")):
                candidate = "point"
            else:
                candidate = "idle"
        elif keep_grip:
            candidate = "point"
        elif not number_pad and fingers == (True, True, True, False):
            candidate = "zoom"
        elif not number_pad and fingers == (True, True, False, False):
            candidate = "scroll"
        elif palm_mode:
            candidate = "point"
        elif self.click_gesture == "Pinch focus" and not any(fingers[1:]):
            candidate = self.focus_pose(joints, pinch_gap)
        elif self.click_gesture != "Pinch focus" and (
            pointing or (self.click_gesture == "Point + pinch" and self.pose == "point"
                         and not any(fingers[1:]) and pinch_gap < 0.53)
        ):
            candidate = "point"
        else:
            candidate = "idle"

        right_click = right_frozen = False
        # Start a right pinch only from an active pointer, outside the other hand commands.
        right_aim = self.pinch_pointer if focus_mode and not self.camera_aim else self.cursor.output
        if self.camera_aim:
            right_aim = self.camera_pointer
        aiming = candidate in ("point", "idle", "waiting_to_aim")
        swipe_tail = self.swipe_enabled and self.swipe.tail_active and not number_pad
        motion_guard = (unstable_motion and not number_pad and not self.pinched and not self.right_held
                        and self.air_tap.clicked is None)
        suppress_clicks = swipe_tail or motion_guard
        if (not relaxed_mode and not suppress_clicks and self.pinch_scale_ready and not number_pad and not second_open and not open_palm
                and self.pose == "point" and right_aim is not None and not self.pinched
                and self.air_tap.clicked is None and (aiming or self.right_held)):
            right_frozen, right_click = self.right_pinch(
                points, world, image_to_world, pinch_gap, now, may_open=candidate == "point",
                left_contact=pinch_gap <= PINCH_CONTACT or assisted_contact,
            )
            if right_frozen:
                candidate = "point"
        elif self.right_held and not number_pad and not suppress_clicks:
            # A different gesture can take priority, while this squeeze still owns the button.
            self.right_pinch(points, world, image_to_world, pinch_gap, now, may_open=False)
        else:
            self.release_right_pinch()

        if self.swipe_enabled:
            allowed = (not number_pad and second is None and (palm_mode or not open_palm) and self.pinch_scale_ready
                       and (not relaxed_mode or (shape == "ready" and not self.relaxed_clicks.latched))
                       and candidate == self.pose == "point" and pinch_gap >= 0.36
                       and not closing and not self.pinched and not self.contact_frames
                       and not right_frozen and not self.right_held
                       and self.air_tap.anchor is None)
            swipe_point = palm_point if allowed or swipe_tail else None
            self.screen_swipe = self.swipe.update(swipe_point, normalized_span, now)
            swipe_tail = self.swipe.settling(now) and not number_pad
        suppress_clicks = swipe_tail or motion_guard
        if relaxed_mode and (suppress_clicks or second_open or candidate != "point"):
            self.relaxed_clicks.reset()
        if suppress_clicks:
            self.air_tap.reset()
            self.release_pinch()
            self.release_right_pinch()
            if swipe_tail and self.swipe.hold_after_switch and candidate == self.pose == "point":
                return self.hold_aim(right_aim)

        if candidate != self.candidate:
            self.candidate = candidate
            self.frames = 0
        self.frames += 1
        if palm_mode and candidate != "point" and self.frames < 3:
            if self.pose not in ("idle", "point"):
                return Gesture("idle")
            candidate = "point"
        if candidate != "point":
            self.air_tap.reset()
            self.release_pinch()
            if candidate == "waiting_to_aim":
                self.cursor.hold()
        if candidate != self.pose:
            if not palm_mode and self.frames < 2 and not (relaxed_mode and candidate == "point"):
                return Gesture("waiting_to_aim" if candidate == "waiting_to_aim" else "idle")
            self.pose = candidate
            self.previous_y = None
            self.travel = 0.0
            self.cursor.hold()

        if self.pose == "point":
            if relaxed_mode:
                frozen, button = False, None
                if self.pinch_scale_ready and not suppress_clicks and not second_open:
                    frozen, button = self.relaxed_clicks.update(shape, now)
                if frozen:
                    if button == "left" and self.can_drag(number_pad):
                        position = self.aim_position(knuckles, width, height)
                        area = aim_area(self.click_gesture, self.palm_travel, number_pad=number_pad)
                        self.drag.press(self.aim_target(position, area)[1], now, self.response)
                        return self.hold_aim(right_aim, press=True)
                    return self.hold_aim(right_aim, click=button == "left", right_click=button == "right" and not number_pad)
                if shape != "ready":
                    return Gesture("idle")
                position = self.aim_position(knuckles, width, height)
                area = aim_area(self.click_gesture, self.palm_travel, number_pad=number_pad)
                return self.point(position, now, False, False, area)
            if right_frozen or self.right_held:
                self.release_pinch()
                self.air_tap.reset()
                return self.hold_aim(right_aim, right_click=right_click)
            press = False
            if pinch_mode:
                reopen = 0.42 if self.click_gesture == "Point + pinch" else PINCH_READY_MIN
                click = (not suppress_clicks
                         and self.pinch(pinch_gap, now, reopen=reopen, assisted_contact=assisted_contact))
                self.previous_gap = pinch_gap
                press = click and self.can_drag(number_pad)
                click = click and not press
            if palm_mode:
                frozen = self.pinched or (self.armed and (assisted_contact or (closing and pinch_gap < 0.38)))
                position = self.aim_position(knuckles, width, height)
                area = aim_area(self.click_gesture, self.palm_travel, number_pad=number_pad)
                return self.point(position, now, frozen, click, area, press=press)
            tip = aim_landmark(points, self.click_gesture)
            position = self.aim_position(tip, width, height)
            if self.click_gesture == "Air tap":
                if suppress_clicks:
                    return self.point(position, now, False, False)
                ratio = depth / self.reference_depth if depth is not None else 1.0
                planar = self.position(tip, width, height, ratio=ratio)
                frozen, click = self.air_tap.update(depth, normalized_span, planar, now)
                return self.point(position, now, frozen, click)
            if self.click_gesture == "Point + pinch":
                frozen = not suppress_clicks and (self.pinched or assisted_contact or pinch_gap < 0.34)
                return self.point(position, now, frozen, click, press=press)

            held = not suppress_clicks and (self.pinched or assisted_contact or pinch_gap < PINCH_HOLD)
            frozen = held or (closing and not suppress_clicks)
            if number_pad or self.camera_aim:
                return self.point(position, now, frozen, click, press=press)
            if held:
                self.pinch_sample = None
            return self.focus_point(position, now, frozen, click, desktop_pointer)
        if self.pose in ("scroll", "zoom"):
            return self.vertical(self.position(palm_center(points), width, height)[1], self.pose)
        if self.pose == "two_scroll":
            second_y = float(palm_center(second)[1] / height)
            other_depth = relative_depth(second, second_world, width)
            if other_depth is not None:
                second_y = 0.5 + (second_y - 0.5) * self.depth_scale(other_depth)
            return self.vertical(second_y, "scroll")
        if self.pose == "two_zoom":
            span = dist(palm_center(points), palm_center(second)) / width
            other_depth = relative_depth(second, second_world, width)
            if depth is not None and other_depth is not None:
                span *= (depth + other_depth) * 0.5 / self.reference_depth
            return self.vertical(-log(max(span, 0.02)), "zoom", step_size=0.14)
        return Gesture(candidate)

    def unstable_palm_motion(self, point, span, now):
        previous = self.last_palm_motion
        self.last_palm_motion = point, span, now
        if previous is None or not 0 < now - previous[2] <= 0.18:
            self.palm_motion_until = 0.0
            self.palm_quiet_frames = 0
            return False
        previous_point, previous_span, last_time = previous
        step = dist(point, previous_point)
        # A big step by a small hand can leave the landmark crop catching up for a few frames.
        small_span = min(span, previous_span)
        if (small_span < SMALL_MOVING_PALM_SPAN and step >= max(0.018, 0.30 * small_span)
                and step / (now - last_time) >= 0.33):
            self.palm_motion_until = now + PALM_MOTION_SETTLE_TIME
        if now >= self.palm_motion_until:
            self.palm_quiet_frames = 0
            return False
        self.palm_quiet_frames = self.palm_quiet_frames + 1 if step <= max(0.004, 0.10 * span) else 0
        if self.palm_quiet_frames >= 2:
            self.palm_motion_until = 0.0
        return True

    def update_pinch_scale(self, span):
        if self.pinch_scale_ready:
            return
        # Distant landmarks can briefly change size as the initial crop settles. Aim immediately,
        # but only arm a click after several similarly sized observations.
        if self.pinch_span_range is None and span >= SMALL_PALM_SPAN:
            self.pinch_scale_ready = True
            return
        low, high = self.pinch_span_range or (span, span)
        low, high = min(low, span), max(high, span)
        if span <= 0 or low < PALM_SCALE_AGREEMENT * high:
            self.pinch_span_range = span, span
            self.pinch_scale_samples = 1
            return
        self.pinch_span_range = low, high
        self.pinch_scale_samples += 1
        self.pinch_scale_ready = self.pinch_scale_samples >= PALM_SCALE_SAMPLES

    def update_depth(self, depth, now):
        if depth is None:
            return
        if self.reference_depth is None:
            self.reference_depth = depth
        dt = 1 / 30 if self.depth_time is None else min(0.08, max(0.005, now - self.depth_time))
        self.depth_ratio += weight(7, dt) * (self.depth_scale(depth) - self.depth_ratio)
        self.depth_time = now

    def depth_scale(self, depth):
        return 1.0 if self.reference_depth is None else min(1.34, max(0.74, depth / self.reference_depth))

    def position(self, tip, width, height, ratio=None):
        if ratio is None:
            ratio = self.depth_ratio
        return (
            float(0.5 + (tip[0] / width - 0.5) * ratio),
            float(0.5 + (tip[1] / height - 0.5) * ratio),
        )

    def aim_position(self, tip, width, height):
        if self.camera_aim:
            return float(tip[0] / max(1, width - 1)), float(tip[1] / max(1, height - 1))
        return self.position(tip, width, height)

    def focus_pose(self, joints, gap):
        if (self.armed or self.pinched) and gap < PINCH_READY_MIN:
            # Tip contact can hide joints the ready-pose check needs.
            return "point"
        reach, thumb_advance = pinch_shape(joints)
        if self.armed:
            thumb_min = 0.08 if gap < 0.76 else 0.13
            ready = gap <= PINCH_READY_MAX + 0.06 and reach >= 0.30 and thumb_advance >= thumb_min
        else:
            ready = PINCH_READY_MIN <= gap <= PINCH_READY_MAX and reach >= 0.35 and thumb_advance >= 0.13
        return "point" if ready else "waiting_to_aim"

    def pad_contact(self, lateral, depth, gap, now):
        # Touching finger pads can leave their estimated centers apart after a squeeze.
        if not self.armed or self.pinch_opening is None or lateral > 0.32 or depth > 0.14:
            return False
        open_gap, opened_at = self.pinch_opening
        return 0 <= now - opened_at <= PINCH_PAD_WINDOW and open_gap - gap >= PINCH_PAD_SQUEEZE

    def arm_pinch(self, gap, now):
        self.armed = True
        if gap >= PINCH_PAD_OPENING:
            self.pinch_opening = gap, now

    def pinch(self, gap, now, reopen, assisted_contact):
        if not self.pinch_scale_ready:
            return False
        approaching = observed_squeeze(self.left_gaps, gap, now)
        if self.pinched:
            # Assisted contact can start above the usual reopen threshold.
            reopen = max(reopen, self.pinch_closed_gap + 0.04)
            self.reopen_frames = self.reopen_frames + 1 if gap >= reopen and not assisted_contact else 0
            if self.reopen_frames and (gap >= reopen + 0.06 or self.reopen_frames >= 2):
                self.pinched = False
                self.arm_pinch(gap, now)
            return False

        if self.contact_frames and gap >= max(reopen, PINCH_CONTACT_ASSIST + 0.05):
            self.pinch_opening = None
        self.contact_frames = self.contact_frames + 1 if assisted_contact else 0
        if self.armed and (gap <= PINCH_CONTACT or self.contact_frames >= 2 or (assisted_contact and approaching)):
            self.armed = False
            self.pinched = True
            self.pinch_closed_gap = gap
            self.pinch_opening = None
            self.contact_frames = self.reopen_frames = 0
            return True
        if gap >= reopen and not assisted_contact:
            self.arm_pinch(gap, now)
        return False

    def right_pinch(self, points, world, image_to_world, index_gap, now, *, may_open, left_contact=False):
        lateral, depth = pinch_separation(points, world, image_to_world, tip=12)
        gap = hypot(lateral, depth)
        index_clear = index_gap >= RIGHT_INDEX_CLEAR
        if self.right_held:
            if gap < RIGHT_OPEN or not index_clear:
                return True, False
            self.release_right_pinch()

        approaching = observed_squeeze(self.right_gaps, gap, now)
        if gap >= RIGHT_OPEN and index_clear:
            if not may_open:
                self.release_right_pinch()
                return False, False
            reach, advance = pinch_shape(points if world is None else world, tip=12)
            self.right_opening = (gap, now) if gap <= 0.90 and reach >= 0.36 and advance >= 0.10 else None
            self.right_contact_frames = 0
            return False, False
        if self.right_opening is None:
            return False, False

        opening, opened_at = self.right_opening
        if not 0 <= now - opened_at <= PINCH_PAD_WINDOW:
            self.release_right_pinch()
            return False, False
        closing = gap <= RIGHT_CLOSING and (opening - gap >= RIGHT_SQUEEZE or approaching)
        if not closing:
            self.right_contact_frames = 0
            if not index_clear:
                self.release_right_pinch()
            return False, False

        if not index_clear:
            if left_contact and gap - index_gap >= 0.10:
                # A clearly closer index is still an ordinary left pinch.
                self.release_right_pinch()
                return False, False
            # A squeeze involving both fingers is ambiguous. Wait for both to reopen.
            self.right_contact_frames = 0
            if left_contact:
                self.right_held = True
            return True, False

        clear_squeeze = approaching or (opening >= PINCH_PAD_OPENING and opening - gap >= PINCH_PAD_SQUEEZE)
        assisted = (gap <= PINCH_CONTACT_ASSIST and
                    (pinch_distance(points, None, tip=12) <= 0.18
                     or (clear_squeeze and lateral <= 0.32 and depth <= 0.14)))
        self.right_contact_frames = self.right_contact_frames + 1 if assisted else 0
        click = gap <= PINCH_CONTACT or self.right_contact_frames >= 2 or (assisted and approaching)
        self.right_held = click
        return True, click

    def focus_point(self, position, now, frozen, click, desktop_pointer):
        if frozen:
            return Gesture("point", self.pinch_pointer, click=click, aim_held=True)

        x, y = self.pointer_coordinates(position)
        if self.pinch_sample is None:
            if desktop_pointer is not None:
                self.pinch_pointer = desktop_pointer()
            elif self.pinch_pointer is None:
                self.pinch_pointer = bound_screen(x), bound_screen(y)
            self.pinch_aim.reset()
            self.pinch_sample = self.pinch_aim.update(x, y, now, self.response)
            return Gesture("point", self.pinch_pointer)

        previous_x, previous_y = self.pinch_sample
        next_x, next_y = self.pinch_aim.update(x, y, now, self.response)
        cursor_x, cursor_y = self.pinch_pointer
        self.pinch_pointer = (
            bound_screen(cursor_x + (next_x - previous_x)),
            bound_screen(cursor_y + (next_y - previous_y)),
        )
        self.pinch_sample = next_x, next_y
        return Gesture("point", self.pinch_pointer)

    @staticmethod
    def pointer_coordinates(position, area=ACTIVE_AREA):
        left, top, right, bottom = area
        return (position[0] - left) / (right - left), (position[1] - top) / (bottom - top)

    def aim_target(self, position, area=None):
        """Where the hand aims on the screen: unclamped, so reaching past the box edge shows, and clamped."""
        if self.camera_aim and self.reach is not None:
            x, y = self.reach.pointer(position, self.reach_scale)
        else:
            if area is None:
                area = FULL_FRAME if self.camera_aim else ACTIVE_AREA
            x, y = self.pointer_coordinates(position, area)
        return (x, y), (bound_screen(x), bound_screen(y))

    def point(self, position, now, frozen, click, area=None, press=False):
        if press:
            self.drag.press(self.aim_target(position, area)[1], now, self.response)
            frozen = True
        if frozen:
            pointer = self.camera_pointer if self.camera_aim else self.cursor.output
            return self.hold_aim(pointer, click=click, press=press)
        self.aim_paused = False
        (x, y), target = self.aim_target(position, area)
        if self.camera_aim:
            if self.camera_reseed:
                self.cursor.reset()
                self.camera_reseed = False
            self.camera_pointer = self.cursor.update(*target, now, self.response, stabilize=self.click_gesture == RELAXED_HAND)
            return Gesture("point", self.camera_pointer, camera_pointer=self.camera_pointer, raw_pointer=(x, y))
        return Gesture("point", self.cursor.update(*target, now, self.response, stabilize=self.click_gesture == RELAXED_HAND))

    def hold_aim(self, pointer, *, click=False, right_click=False, press=False):
        if not self.aim_paused:
            self.cursor.hold()
        self.aim_paused = True
        return Gesture("point", pointer, click=click, aim_held=True, right_click=right_click, press=press, held=press)

    def can_drag(self, number_pad):
        # The number pad types a key per squeeze. Without camera aim, Pinch focus steers relatively.
        return (self.drag_enabled and not number_pad
                and self.click_gesture in (RELAXED_HAND, "Palm + pinch", "Point + pinch", "Pinch focus")
                and (self.camera_aim or self.click_gesture != "Pinch focus"))

    def drag_aim(self, points, knuckles, width, height, number_pad):
        if self.click_gesture in PALM_MODES:
            area = aim_area(self.click_gesture, self.palm_travel, number_pad=number_pad)
            return self.aim_position(knuckles, width, height), area
        return self.aim_position(aim_landmark(points, self.click_gesture), width, height), None

    def follow_drag(self, points, knuckles, width, height, now, number_pad, pinch_gap, middle_gap, assisted,
                    second_open):
        """Keep the button down while the grip stays closed, moving with the hand once it travels."""
        frames = 2
        if number_pad or second_open:
            contact, frames = False, 1
        elif self.click_gesture == RELAXED_HAND:
            near, far = sorted((pinch_gap, middle_gap))
            contact = near <= RELAXED_HOLD and far <= RELAXED_HOLD_MAX
        else:
            reopen = max(0.42 if self.click_gesture == "Point + pinch" else PINCH_READY_MIN,
                         self.pinch_closed_gap + 0.04)
            contact = pinch_gap < reopen or assisted
            if pinch_gap >= reopen + 0.06:
                frames = 1
        position, area = self.drag_aim(points, knuckles, width, height, number_pad)
        release, motion = self.drag.update(contact, self.aim_target(position, area)[1], now, self.response, frames)
        if release:
            return self.end_drag(now)
        return Gesture("point", None, aim_held=motion is None, held=True, drag_motion=motion,
                       drag_hand=self.drag.hand)

    def end_drag(self, now, lost=False):
        self.drag.reset()
        # Aim restarts from the hand: the app carries the pointer on from where the button came up.
        self.recenter_aim()
        self.release_pinch()
        if lost:
            self.reset(keep_pointer=True)
            return Gesture("lost", release=True)
        if self.click_gesture == RELAXED_HAND:
            # Stay put until the hand is back in the aiming pose, as after a click.
            self.relaxed_clicks.latch(now)
        return Gesture("point", None, aim_held=True, release=True)

    def cancel_drag(self):
        """Forget a held press whose button was let go elsewhere (pause, number pad, a lost pointer)."""
        if self.drag.active:
            self.drag.reset()
            self.recenter_aim()
            self.release_pinch()
            self.relaxed_clicks.reset()

    def vertical(self, y, pose, step_size=None):
        if step_size is None:
            step_size = 0.028 if pose == "scroll" else 0.10
        if self.previous_y is not None:
            delta = self.previous_y - y
            limit = max(0.12, step_size)
            self.travel += max(-limit, min(limit, delta))
        self.previous_y = y
        limit = 2 if pose == "scroll" else 1
        steps = max(-limit, min(limit, int(self.travel / step_size)))
        if steps:
            remainder = self.travel - steps * step_size
            self.travel = copysign(min(abs(remainder), step_size), remainder)
        return Gesture(pose, steps=steps)
