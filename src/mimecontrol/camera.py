import sys
from collections import deque
from contextlib import closing, suppress
from dataclasses import dataclass, replace
from math import isfinite
from threading import Event, Lock, Thread
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy import ndarray

from .calibration import palm_scale
from .camera_filters import CAMERA_FILTERS, CameraFilter
from .gestures import CLICK_GESTURES, DEFAULT_PALM_TRAVEL, FULL_FRAME, aim_area, aim_landmark

FRAME_INTERVAL = 1 / 30
TRACKING_SIZE = (640, 480)
PREVIEW_SIZE = (320, 240)


def fit_dimensions(width, height, bounds):
    max_width, max_height = bounds
    scale = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def preferred_native_source(profiles):
    """Prefer an already-available stream if it adds detail without a slower frame rate."""
    if not profiles or profiles[0] is None:
        return 0
    native_width, native_height, native_fps = profiles[0]
    fit_width, fit_height = fit_dimensions(native_width, native_height, TRACKING_SIZE)
    native_detail = fit_width * fit_height
    best = (native_detail, -native_width * native_height)
    selected = 0
    for index, profile in enumerate(profiles[1:], start=1):
        if profile is None:
            continue
        width, height, fps = profile
        # Treat nominal 30 and NTSC 29.97 (or 60 and 59.94) as equivalent.
        if (width * native_height != height * native_width or fps < 0.99 * min(native_fps, 1 / FRAME_INTERVAL)
                or width > 2 * TRACKING_SIZE[0] or height > 2 * TRACKING_SIZE[1]):
            continue
        fit_width, fit_height = fit_dimensions(width, height, TRACKING_SIZE)
        detail = fit_width * fit_height
        score = (detail, -width * height)
        if detail > native_detail and score > best:
            selected, best = index, score
    return selected


UNCOMPRESSED = ("NV12", "YUY2")


def oversized(width, height):
    return width > 2 * TRACKING_SIZE[0] or height > 2 * TRACKING_SIZE[1]


def efficient_format(formats, current):
    """Pick the lightest video mode that keeps the current view and the detail tracking uses.

    formats and current are (subtype, width, height, fps). Returns an index into
    formats, or None to keep the current mode. A 4K webcam converts and copies
    every full-size frame, which can starve tracking of time.
    """
    subtype, width, height, fps = current
    if not oversized(width, height):
        return None
    needed = fit_dimensions(width, height, TRACKING_SIZE)
    wanted_fps = 1 / FRAME_INTERVAL
    best = None
    for index, (kind, w, h, rate) in enumerate(formats):
        # Same aspect ratio keeps the field of view on typical binning sensors.
        if (w * height != h * width or rate < 0.97 * min(fps, wanted_fps)
                or fit_dimensions(w, h, TRACKING_SIZE) != needed):
            continue
        # Prefer the frame rate tracking uses, then formats that need no decoding, then fewer pixels.
        score = (abs(rate - wanted_fps) > 1, str(kind).upper() not in UNCOMPRESSED, w * h, rate)
        if w * h < width * height and (best is None or score < best[0]):
            best = score, index
    return None if best is None else best[1]


def has_image(frame):
    if frame is None or not frame.size:
        return False
    height, width = frame.shape[:2]
    return frame[::max(1, height // 24), ::max(1, width // 32)].max() > 8


def request_wide_view(cv2, capture):
    # OpenCV doesn't expose a zoom range. Some drivers use zero for the uncropped view
    # or clamp the request to their minimum. Ignore drivers without usable zoom controls.
    with suppress(AttributeError, cv2.error, OSError):
        previous = capture.get(cv2.CAP_PROP_ZOOM)
        if isfinite(previous) and previous > 0 and capture.set(cv2.CAP_PROP_ZOOM, 0):
            selected = capture.get(cv2.CAP_PROP_ZOOM)
            if isfinite(selected) and 0 <= selected < previous:
                return previous, selected
    return None


def lighten_capture(cv2, capture, frame):
    """Ask an OpenCV driver for a smaller mode with the same aspect ratio, or keep the current one."""
    height, width = frame.shape[:2]
    if not oversized(width, height):
        return frame
    target = fit_dimensions(width, height, TRACKING_SIZE)
    with suppress(cv2.error, OSError):
        if capture.set(cv2.CAP_PROP_FRAME_WIDTH, target[0]) and capture.set(cv2.CAP_PROP_FRAME_HEIGHT, target[1]):
            for _ in range(8):
                ok, smaller = capture.read()
                if ok and smaller is not None and smaller.size:
                    h, w = smaller.shape[:2]
                    if w < width and abs(w * height - h * width) <= 0.01 * width * height:
                        return smaller
                    break
        # The driver didn't keep the view, so restore its original mode.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        for _ in range(8):
            ok, restored = capture.read()
            if ok and restored is not None and restored.size:
                return restored
    return frame


def restore_zoom(cv2, capture, change):
    if change is not None:
        previous, selected = change
        with suppress(cv2.error, OSError):
            if capture.get(cv2.CAP_PROP_ZOOM) == selected:
                capture.set(cv2.CAP_PROP_ZOOM, previous)


@dataclass(frozen=True)
class Sample:
    timestamp: float
    width: int
    height: int
    points: "ndarray | None"
    preview: bytes | None = None
    world: "ndarray | None" = None
    second: "ndarray | None" = None
    second_world: "ndarray | None" = None
    sensor_time: float | None = None
    depth_m: float | None = None
    second_depth_m: float | None = None


class CameraSession:
    def __init__(self, camera_index, preview, tracking=True, two_hands=False, *,
                 mode=CLICK_GESTURES[0], palm_travel=DEFAULT_PALM_TRAVEL, camera_filter=CAMERA_FILTERS[0],
                 stereo_profile=None, auxiliary=False, secondary=None, control_names=(), saved_controls=None):
        self.camera_index = camera_index
        self.camera_filter = camera_filter
        self.stereo_profile = stereo_profile
        # A second camera can run before it is aligned, so calibration can align it.
        self.secondary = stereo_profile.secondary if stereo_profile is not None else secondary
        self.auxiliary = auxiliary
        self.aux_camera = None
        self.stereo_status = ""
        # Picture settings. Cameras named in control_names, or with saved settings, open with control
        # first. saved_controls is {camera name: {setting: {"value": n} or {"auto": True}}}.
        self.control_names = frozenset(control_names)
        self.saved_controls = dict(saved_controls or {})
        self.camera_name = None
        self.needed_control = False
        self.camera_controls = None  # The settings the camera reported, once it's streaming.
        self.controls_note = ""  # Why settings are unavailable, if they are.
        self.control_requests = deque()
        self.watch_controls = Event()  # Keep reading automatic values while the settings window is open.
        self.preview_image = None  # The latest preview picture, for windows that only show it.
        self.second_lock = Lock()
        self.history = deque(maxlen=24) if auxiliary or self.secondary is not None else None
        self.reach = None
        self.stop = Event()
        self.preview = Event()
        self.tracking = Event()
        self.two_hands = Event()
        self.eager_second = Event()
        self.set_pointer_mode(mode, palm_travel)
        self.set_number_pad(False)
        self.set_two_hands(two_hands)
        if tracking:
            self.tracking.set()
        self.set_preview(preview)
        self.lock = Lock()
        self.latest = None
        self.error = None
        self.backend = None
        self.connection_status = "Starting camera…"
        self.thread = Thread(target=self.run, daemon=True, name="webcam")
        self.thread.start()

    def set_preview(self, enabled):
        if enabled:
            self.preview.set()
        else:
            self.preview.clear()

    def set_two_hands(self, enabled):
        if enabled:
            self.two_hands.set()
        else:
            self.two_hands.clear()

    def set_eager_second(self, enabled):
        """Refresh the other hand every frame, as two-handed calibration needs."""
        if enabled:
            self.eager_second.set()
        else:
            self.eager_second.clear()

    def use_secondary(self, secondary, profile):
        """Add a second camera, or its alignment, before tracking starts, such as after a camera check."""
        if self.aux_camera is not None and secondary != self.secondary:
            return  # A different second camera is already running until the camera restarts.
        self.secondary, self.stereo_profile = secondary, profile
        with self.lock:
            if secondary is not None and self.history is None:
                self.history = deque(maxlen=24)

    def set_pointer_mode(self, mode, palm_travel):
        self.pointer_mode = mode, palm_travel

    def set_number_pad(self, enabled):
        self.number_pad = enabled

    def change_control(self, key, value=None, auto=None):
        """Change a picture setting from any thread. The camera thread applies it between frames."""
        self.control_requests.append((key, value, auto))

    def prefers_control(self, name):
        return name in self.control_names or bool(self.saved_controls.get(name))

    def remember_controls(self, name, choices):
        """Keep the settings window's latest choices, so a reconnect applies them rather than older ones."""
        self.saved_controls = {**self.saved_controls, name: dict(choices)}
        aux = self.aux_camera
        if aux is not None:
            aux.saved_controls = self.saved_controls

    def start_second_camera(self):
        with self.second_lock:
            if self.secondary is not None and self.aux_camera is None and not self.auxiliary:
                self.aux_camera = CameraSession(self.secondary, False, auxiliary=True,
                                                two_hands=self.two_hands.is_set(), camera_filter=self.camera_filter,
                                                control_names=self.control_names, saved_controls=self.saved_controls)

    def take_sample(self):
        with self.lock:
            sample, self.latest = self.latest, None
        return sample

    def recent_samples(self):
        with self.lock:
            return tuple(self.history) if self.history is not None else ()

    def confirm_windows_video(self, capture):
        deadline = monotonic() + 1.5
        count = 0
        last_frame = 0.0
        saw_black = False
        while not self.stop.is_set() and monotonic() < deadline:
            ok, frame = capture.read()
            if ok and has_image(frame):
                now = monotonic()
                count = count + 1 if now - last_frame < 0.22 else 1
                last_frame = now
                if count == 3:
                    return frame, ""
            elif ok and frame is not None and frame.size:
                saw_black = True
            self.stop.wait(0.01)
        reason = "sent black or intermittent frames" if saw_black else "did not send steady video"
        return None, reason

    def connections(self, cv2):
        windows = sys.platform == "win32"
        native_error = ""
        if windows:
            self.connection_status = f"Trying camera {self.camera_index} with Windows Camera API…"
            native = None
            try:
                from .windows_camera import WindowsCamera

                native = WindowsCamera(cv2, self.stop)
                name, frame = native.connect(self.camera_index, self.confirm_windows_video, self.prefers_control)
                self.camera_name, self.needed_control = name, native.needed_control
                self.backend = f"Windows Camera API · {name}"
                yield native, frame
                native_error = f"{name} stopped sending usable video"
            except Exception as exc:
                code = getattr(exc, "winerror", None)
                if code in (5, -2147024891, 0x80070005):
                    raise RuntimeError(
                        "Windows denied webcam access (0x80070005). Turn on Let desktop apps access "
                        "your camera in Windows Camera privacy settings."
                    ) from exc
                native_error = str(exc) or type(exc).__name__
            finally:
                if native is not None:
                    with suppress(OSError):
                        native.release()

            # Prefer Media Foundation over DirectShow, which can return an intermittent black feed.
            drivers = (("Media Foundation", cv2.CAP_MSMF), ("DirectShow", cv2.CAP_DSHOW))
            capture_size = (640, 480)
            help_text = (
                "In Windows Settings → Camera, enable Camera access and desktop app access. "
                "Close other camera apps, then try again or choose another camera number."
            )
        else:
            drivers = (("system driver", cv2.CAP_ANY),)
            capture_size = (320, 240)
            help_text = "Check camera permissions, close other camera apps, or choose another camera number."

        opened = False
        stream_issue = "did not send usable video"
        # The driver's native format may cover more of the sensor than a forced 4:3 mode.
        modes = (False, True)
        for name, driver in drivers:
            for reduced_size in modes:
                if self.stop.is_set():
                    return None
                mode = "" if reduced_size else " at default settings"
                self.connection_status = f"Trying camera {self.camera_index} with {name}{mode}…"
                capture = None
                zoom_change = None
                device_open = False
                try:
                    capture = cv2.VideoCapture(self.camera_index, driver)
                    device_open = capture.isOpened()
                    if not device_open:
                        break
                    opened = True
                    if reduced_size:
                        # Some Windows drivers open successfully but fail to stream at 320×240.
                        capture.set(cv2.CAP_PROP_FRAME_WIDTH, capture_size[0])
                        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, capture_size[1])
                        if not windows:
                            capture.set(cv2.CAP_PROP_FPS, 30)
                    if sys.platform == "linux":
                        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    zoom_change = request_wide_view(cv2, capture)
                    if windows:
                        first_frame, stream_issue = self.confirm_windows_video(capture)
                    else:
                        first_frame = None
                        for _ in range(8):
                            ok, frame = capture.read()
                            if ok and frame is not None and frame.size:
                                first_frame = frame
                                break
                            if self.stop.wait(0.08):
                                return
                    if first_frame is not None and not reduced_size:
                        first_frame = lighten_capture(cv2, capture, first_frame)
                    if first_frame is not None:
                        self.backend = name
                        yield capture, first_frame
                        stream_issue = "stopped sending video after connecting"
                except cv2.error:
                    # A driver's open, mode change, or read can all raise a native error.
                    if not device_open:
                        break
                finally:
                    if capture is not None:
                        restore_zoom(cv2, capture, zoom_change)
                        capture.release()

        if self.stop.is_set():
            return None
        if windows:
            problem = stream_issue if opened else "could not open in either legacy driver"
            raise RuntimeError(
                f"Camera {self.camera_index} {problem}. Windows Camera API reported {native_error}. {help_text}"
            )
        problem = "opened but did not send video" if opened else "could not open"
        raise RuntimeError(f"Camera {self.camera_index} {problem}. {help_text}")

    def run(self):
        try:
            import cv2

            if self.tracking.is_set():
                # Open the second camera alongside this one rather than after it.
                self.start_second_camera()
            with closing(self.connections(cv2)) as connections:
                for capture, first_frame in connections:
                    self.stream(cv2, capture, first_frame)
                    if self.stop.is_set():
                        return
                    self.backend = None
                    self.connection_status = "Camera video stopped. Trying another driver or setting…"
                    with self.lock:
                        self.latest = None
                        if self.history is not None:
                            self.history.clear()
        except Exception as exc:
            if not self.stop.is_set():
                self.error = str(exc)
        finally:
            if self.aux_camera is not None:
                self.aux_camera.stop.set()
                self.aux_camera.thread.join(10)

    def stereo_sample(self, sample):
        aux = self.aux_camera
        profile = self.stereo_profile
        others = aux.recent_samples()
        other = others[-1] if others else None
        if aux.error:
            self.stereo_status = f"Single camera · Camera {aux.camera_index} unavailable"
            return sample
        if other is not None and self.stereo_profile.sizes != ((sample.width, sample.height), (other.width, other.height)):
            self.stereo_status = "Single camera · Camera format changed. Realign cameras."
            return sample
        world = other_hand = None
        primary = sample
        if sample.points is not None and others:
            primary, other = self.stereo_frames(sample, others)
            world, other_hand = profile.match_hands(primary, other)
        if world is None and other_hand is None:
            self.stereo_status = "Single camera · Waiting for a matching second view"
            return sample
        changes = {}
        if other_hand is not None:
            changes["second_depth_m"] = float(other_hand[[0, 5, 9, 13, 17], 2].mean())
        if world is None:
            return replace(sample, **changes)
        depth_m = float(world[[0, 5, 9, 13, 17], 2].mean())
        estimate = "about " if profile.approximate else ""
        self.stereo_status = f"Stereo depth · {estimate}{round(depth_m * 100)} cm"
        # A slower second camera may only match an earlier primary frame.
        # Its depth can inform reach mapping while the live hand drives gestures.
        hand = world - world[0] if primary is sample else sample.world
        return replace(sample, world=hand, depth_m=depth_m, **changes)

    def stereo_frames(self, sample, others):
        from .stereo import PAIR_WINDOW

        profile = self.stereo_profile
        if not profile.sync_clock:
            return sample, min(others[-4:], key=lambda second: profile.pair_error(sample, second))
        current = [second for second in others if second.timestamp >= sample.timestamp - 0.58]
        if current and current[-1].points is not None:
            for first in (sample, *reversed(self.recent_samples())):
                if sample.timestamp - first.timestamp > 0.52:
                    break
                if first.points is None:
                    break
                second = min(current, key=lambda item: profile.pair_error(first, item))
                if second.points is not None and profile.pair_error(first, second) <= PAIR_WINDOW:
                    return first, second
        return sample, None

    def open_settings(self, capture):
        """Apply this camera's saved settings and report what it offers. Runs on the camera thread."""
        settings = capture.camera_settings() if hasattr(capture, "camera_settings") else None
        if settings is None:
            self.camera_controls = None
            self.controls_note = ("Another app is controlling this camera's settings. Close it and turn the camera "
                                  "off and on to change them here." if hasattr(capture, "camera_settings")
                                  else "Camera settings need the Windows camera connection.")
            return None
        saved = self.saved_controls.get(self.camera_name) or {}
        if "zoom" in settings.apply(saved):
            capture.keep_zoom()
        self.camera_controls = settings.snapshot()
        self.controls_note = ""
        return settings

    def handle_settings(self, capture, settings, now, next_refresh):
        """Apply changes from the settings window, and keep automatic values current while it's open."""
        changed = False
        while self.control_requests:
            key, value, auto = self.control_requests.popleft()
            with suppress(AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                if settings.write(key, value, auto) is not None:
                    changed = True
                    if key == "zoom":
                        capture.keep_zoom()
        if self.watch_controls.is_set() and now >= next_refresh:
            next_refresh = now + 0.5
            for control in settings.snapshot():
                if control.auto:
                    with suppress(AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                        settings.refresh(control.key)
                        changed = True
        if changed:
            self.camera_controls = settings.snapshot()
        return next_refresh

    def stream(self, cv2, capture, first_frame):
        try:
            self.stream_frames(cv2, capture, first_frame)
        finally:
            # The settings belong to this connection. A reconnect reads them again.
            self.camera_controls = None
            self.controls_note = ""

    def stream_frames(self, cv2, capture, first_frame):
        cv2.setNumThreads(1)
        exposure = CameraFilter(cv2)
        tracker = None
        last_preview = 0.0
        windows = sys.platform == "win32"
        gaps = 0
        waiting_since = None
        # Read the camera's settings only when there are saved ones to apply or the settings window asks.
        wanted = bool(self.saved_controls.get(self.camera_name))
        settings = self.open_settings(capture) if wanted else None
        settings_read = wanted
        next_refresh = 0.0
        while not self.stop.is_set():
            if not settings_read and (self.watch_controls.is_set() or self.control_requests):
                settings_read = True
                if not getattr(capture, "has_control", True) and hasattr(capture, "take_control"):
                    # A camera shared by choice can't change its settings, so reopen it with control.
                    try:
                        first_frame = capture.take_control(self.confirm_windows_video)
                    except (OSError, RuntimeError):
                        return
                    if first_frame is None:
                        return
                    self.needed_control = capture.has_control
                settings = self.open_settings(capture)
            started = monotonic()
            if settings is not None and (self.control_requests or self.watch_controls.is_set()):
                next_refresh = self.handle_settings(capture, settings, started, next_refresh)
            elif settings is None and settings_read:
                self.control_requests.clear()
            if first_frame is not None:
                ok, frame = True, first_frame
                first_frame = None
            else:
                try:
                    ok, frame = capture.read()
                except (cv2.error, OSError):
                    return
            received = monotonic()
            valid = ok and frame is not None and frame.size
            # Only time spent waiting for the camera counts as a stall. Slow tracking
            # on a busy computer delays the next read but doesn't mean the camera failed.
            if not valid or (windows and not has_image(frame)):
                if waiting_since is None:
                    waiting_since = started
                if received - waiting_since >= 1.2:
                    return
                self.stop.wait(0.04)
                continue
            if windows:
                gaps = gaps + 1 if received - (started if waiting_since is None else waiting_since) > 0.25 else 0
                if gaps >= 3:
                    return
            waiting_since = None
            if tracker is None and self.tracking.is_set():
                from .tracking import HandTracker

                tracker = HandTracker()
                self.start_second_camera()
            if self.aux_camera is not None:
                self.aux_camera.camera_filter = self.camera_filter
                self.aux_camera.set_two_hands(self.two_hands.is_set())
                self.aux_camera.set_eager_second(self.eager_second.is_set())
            preview_due = self.preview.is_set() and received - last_preview >= 0.19
            source_height, source_width = frame.shape[:2]
            # Track small hands from more source detail while fitting the whole camera view.
            bounds = TRACKING_SIZE if tracker is not None else PREVIEW_SIZE
            width, height = fit_dimensions(source_width, source_height, bounds)
            stamp = getattr(capture, "last_frame_time", None)
            sensor_time = stamp.total_seconds() if hasattr(stamp, "total_seconds") else None
            if tracker is not None or preview_due:
                if (width, height) != (source_width, source_height):
                    frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
                frame = cv2.flip(frame, 1)
                frame = exposure.apply(frame, self.camera_filter, received)
            points = world = second = second_world = None
            if tracker is not None:
                points = tracker.track(frame, two_hands=self.two_hands.is_set(), eager_second=self.eager_second.is_set())
                world = tracker.world_landmarks
                second, second_world = tracker.second_landmarks, tracker.second_world_landmarks
            sample = Sample(received, width, height, points, None, world, second, second_world, sensor_time)
            if self.aux_camera is not None and self.stereo_profile is not None:
                sample = self.stereo_sample(sample)
            preview = None
            if preview_due:
                mode, palm_travel = self.pointer_mode
                preview = preview_ppm(cv2, frame, points, tracking=tracker is not None, second=second,
                                      mode=mode, palm_travel=palm_travel, number_pad=self.number_pad, reach=self.reach,
                                      depth_m=sample.depth_m)
                self.preview_image = preview
                last_preview = received
            with self.lock:
                # Keep the pending preview while replacing older tracking data.
                if preview is None and self.latest is not None and self.latest.preview is not None:
                    preview = self.latest.preview
                if preview is not None:
                    sample = replace(sample, preview=preview)
                self.latest = sample
                if self.history is not None:
                    self.history.append(sample)
            self.stop.wait(max(0, FRAME_INTERVAL - (monotonic() - started)))


def preview_ppm(cv2, frame, points, tracking=True, second=None, *,
                mode=CLICK_GESTURES[0], palm_travel=DEFAULT_PALM_TRAVEL, number_pad=False, reach=None, depth_m=None):
    source_height, source_width = frame.shape[:2]
    width, height = fit_dimensions(source_width, source_height, PREVIEW_SIZE)
    if (width, height) != (source_width, source_height):
        frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    scale_x = (width - 1) / max(1, source_width - 1)
    scale_y = (height - 1) / max(1, source_height - 1)
    area = aim_area(mode, palm_travel, number_pad=number_pad) if tracking else FULL_FRAME
    if tracking and reach is not None and not number_pad:
        area = FULL_FRAME
        if points is not None:
            import numpy as np

            # Calibrated reach can be any four-sided shape when the camera is turned.
            outline = reach.outline(reach.mapping_scale(palm_scale(points, source_width), depth_m))
            if outline is not None:
                corners = np.round(np.array(outline) * (width - 1, height - 1)).astype(np.int32)
                cv2.polylines(frame, [corners.reshape(-1, 1, 2)], True, (180, 190, 186), 1)
    if area != FULL_FRAME:
        left, top, right, bottom = area
        span_x, span_y = (width, height) if number_pad else (width - 1, height - 1)
        cv2.rectangle(frame, (round(left * span_x), round(top * span_y)),
                      (round(right * span_x), round(bottom * span_y)), (180, 190, 186), 1)
    for hand, color in ((points, (148, 231, 128)), (second, (110, 192, 255))):
        if hand is not None:
            for x, y, _ in hand:
                cv2.circle(frame, (round(float(x) * scale_x), round(float(y) * scale_y)), 2, color, -1)
    if tracking and points is not None:
        x, y = aim_landmark(points, mode)
        center = round(float(x) * scale_x), round(float(y) * scale_y)
        cv2.circle(frame, center, 6, (35, 48, 45), 2)
        cv2.circle(frame, center, 5, (255, 255, 255), 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return f"P6\n{width} {height}\n255\n".encode("ascii") + rgb.tobytes()
