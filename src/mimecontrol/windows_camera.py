"""Capture color video with Windows MediaCapture on the camera worker thread."""

from contextlib import suppress
from ctypes import byref, windll
from ctypes.wintypes import MSG
from math import isfinite
from time import monotonic, sleep

import numpy as np
from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
from winrt.windows.foundation import AsyncStatus
from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.capture import (
    MediaCapture,
    MediaCaptureInitializationSettings,
    MediaCaptureMemoryPreference,
    MediaCaptureSharingMode,
    MediaStreamType,
    StreamingCaptureMode,
)
from winrt.windows.media.capture.frames import (
    MediaFrameReaderAcquisitionMode,
    MediaFrameReaderStartStatus,
    MediaFrameSourceGroup,
    MediaFrameSourceKind,
)
from winrt.windows.media.mediaproperties import MediaEncodingSubtypes
from winrt.windows.storage.streams import Buffer

from .camera import efficient_format, preferred_native_source
from .camera_controls import CameraControls, WinrtTransport


def source_profile(source):
    # Optional WinRT projections and some camera drivers do not expose usable metadata.
    with suppress(AttributeError, ImportError, OSError, RuntimeError):
        info = source.info
        if info.media_stream_type not in (MediaStreamType.VIDEO_PREVIEW, MediaStreamType.VIDEO_RECORD):
            return None
        device = info.device_information.id
        if not device:
            return None
        current = source.current_format
        video, rate = current.video_format, current.frame_rate
        if video.width > 0 and video.height > 0 and rate.numerator > 0 and rate.denominator > 0:
            return device, video.width, video.height, rate.numerator / rate.denominator
    return None


def camera_names(timeout=4.0):
    """Names of color cameras in the order camera numbers use, or None if Windows doesn't answer."""
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        operation = MediaFrameSourceGroup.find_all_async()
        deadline = monotonic() + timeout
        while operation.status == AsyncStatus.STARTED:
            if monotonic() >= deadline:
                operation.cancel()
                return None
            sleep(0.02)
        return [
            group.display_name
            for group in operation.get_results()
            if any(info.source_kind == MediaFrameSourceKind.COLOR for info in group.source_infos)
        ]
    finally:
        uninit_apartment()


class WindowsCamera:
    def __init__(self, cv2, stop):
        self.cv2 = cv2
        self.stop = stop
        self.capture = None
        self.reader = None
        self.buffer = None
        self.last_frame_time = None
        self.zoom_change = None
        self.has_control = False  # Opened with control, so settings and the video mode can change.
        self.needed_control = False  # A lighter video mode needed control, so open with it first next time.
        self.settings = None
        self.index = None
        self.message = MSG()
        init_apartment(ApartmentType.SINGLE_THREADED)

    def pump_messages(self):
        user32 = windll.user32
        while user32.PeekMessageW(byref(self.message), None, 0, 0, 1):
            user32.TranslateMessage(byref(self.message))
            user32.DispatchMessageW(byref(self.message))

    def wait_for(self, operation):
        deadline = monotonic() + 5
        while operation.status == AsyncStatus.STARTED:
            self.pump_messages()
            if self.stop.wait(0.01) or monotonic() >= deadline:
                operation.cancel()
                reason = "the camera request was cancelled" if self.stop.is_set() else "the camera request timed out"
                raise RuntimeError(reason)
        return operation.get_results()

    def connect(self, index, confirm_video, prefer_control=False):
        """Open camera number index. prefer_control(name) says to take control first, which skips
        a second open for cameras that always need a lighter mode or have saved settings."""
        groups = self.wait_for(MediaFrameSourceGroup.find_all_async())
        color_groups = [
            group for group in groups if any(info.source_kind == MediaFrameSourceKind.COLOR for info in group.source_infos)
        ]
        if index >= len(color_groups):
            raise RuntimeError(f"Windows found {len(color_groups)} color camera(s) for camera {index}")
        group = color_groups[index]
        self.index = index
        sources = None
        if prefer_control(group.display_name) if callable(prefer_control) else prefer_control:
            try:
                sources = self.open(group, MediaCaptureSharingMode.EXCLUSIVE_CONTROL)
                self.has_control = True
            except (OSError, RuntimeError):
                self.close_capture()  # Another app has control. Share the camera instead.
        if sources is None:
            sources = self.open(group, MediaCaptureSharingMode.SHARED_READ_ONLY)
            if sources and self.lighter_format(sources[0]) is not None:
                # Shared access can't change the video mode. Take control of the camera
                # for a lighter mode, and keep sharing it if another app is using it.
                self.close_capture()
                try:
                    sources = self.open(group, MediaCaptureSharingMode.EXCLUSIVE_CONTROL)
                    self.has_control = self.needed_control = True
                except (OSError, RuntimeError):
                    self.close_capture()
                    sources = self.open(group, MediaCaptureSharingMode.SHARED_READ_ONLY)
        if self.has_control and sources:
            try:
                choice = self.lighter_format(sources[0])
                if choice is not None:
                    self.wait_for(sources[0].set_format_async(choice))
            except (OSError, RuntimeError):
                pass  # The current mode still works, only with more work per frame.

        reason = "has no color stream"
        for source in sources:
            for argb in (True, False):
                if self.stop.is_set():
                    raise RuntimeError("the camera request was cancelled")
                self.last_frame_time = None
                try:
                    if argb:
                        operation = self.capture.create_frame_reader_with_subtype_async(source, MediaEncodingSubtypes.argb32)
                    else:
                        operation = self.capture.create_frame_reader_async(source)
                    self.reader = self.wait_for(operation)
                    self.reader.acquisition_mode = MediaFrameReaderAcquisitionMode.REALTIME
                    status = self.wait_for(self.reader.start_async())
                    if status == MediaFrameReaderStartStatus.SUCCESS:
                        frame, reason = confirm_video(self)
                        if frame is not None:
                            return group.display_name, frame
                    else:
                        reason = f"could not start its color stream ({status.name.replace('_', ' ').lower()})"
                except OSError as exc:
                    # Drivers sometimes only support reading in their current format.
                    reason = f"returned {exc}"
                    if getattr(exc, "winerror", 0) in (5, -2147024891, 0x80070005):
                        raise
                if self.reader is not None:
                    self.reader.close()
                    self.reader = None
        raise RuntimeError(f"{group.display_name} {reason}")

    def open(self, group, sharing_mode):
        settings = MediaCaptureInitializationSettings()
        settings.source_group = group
        settings.streaming_capture_mode = StreamingCaptureMode.VIDEO
        settings.memory_preference = MediaCaptureMemoryPreference.CPU
        settings.sharing_mode = sharing_mode
        self.capture = MediaCapture()
        self.wait_for(self.capture.initialize_with_settings_async(settings))
        self.request_wide_view()
        sources = [
            source for source in self.capture.frame_sources.values() if source.info.source_kind == MediaFrameSourceKind.COLOR
        ]
        sources.sort(key=lambda source: source.info.media_stream_type != MediaStreamType.VIDEO_PREVIEW)
        if len(sources) > 1:
            native = source_profile(sources[0])
            if native is not None:
                profiles = [native] + [source_profile(source) for source in sources[1:]]
                preferred = preferred_native_source([
                    profile[1:] if profile is not None and profile[0] == native[0] else None for profile in profiles
                ])
                if preferred:
                    sources.insert(0, sources.pop(preferred))
        return sources

    @staticmethod
    def lighter_format(source):
        with suppress(AttributeError, ImportError, OSError, RuntimeError, TypeError, ZeroDivisionError):
            def describe(item):
                rate = item.frame_rate
                return item.subtype, item.video_format.width, item.video_format.height, rate.numerator / rate.denominator

            formats = list(source.supported_formats)
            choice = efficient_format([describe(item) for item in formats], describe(source.current_format))
            return None if choice is None else formats[choice]
        return None

    def camera_settings(self):
        """This camera's picture settings, or None when another app has control or the driver has none."""
        if not self.has_control or self.capture is None:
            return None
        if self.settings is None:
            try:
                settings = CameraControls(WinrtTransport(self.capture.video_device_controller))
                settings.read_all()
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                return None
            self.settings = settings
        return self.settings

    def take_control(self, confirm_video):
        """Reopen a shared camera with control, so its settings can change. Returns a first frame.
        If another app has control, the camera is shared again and has_control stays False."""
        if self.reader is not None:
            with suppress(OSError, RuntimeError):
                self.reader.close()
            self.reader = None
        self.close_capture()
        self.has_control = False
        _, frame = self.connect(self.index, confirm_video, prefer_control=True)
        return frame

    def keep_zoom(self):
        """You chose a zoom, so leave it as it is when the camera closes."""
        self.zoom_change = None

    def close_capture(self):
        if self.zoom_change is not None:
            control, previous, widest = self.zoom_change
            with suppress(OSError, RuntimeError):
                if control.value == widest:
                    control.value = previous
        self.zoom_change = self.settings = None
        if self.capture is not None:
            with suppress(OSError, RuntimeError):
                self.capture.close()
        self.capture = None

    def request_wide_view(self):
        # Wide view is optional for the driver and must not interrupt shared capture.
        with suppress(AttributeError, ImportError, OSError, RuntimeError):
            control = self.capture.video_device_controller.zoom_control
            if control.supported:
                previous, widest = control.value, control.min
                if isfinite(previous) and isfinite(widest) and widest < previous:
                    control.value = widest
                    self.zoom_change = control, previous, widest

    def read(self, timeout=0.12):
        deadline = monotonic() + timeout
        while not self.stop.is_set():
            self.pump_messages()
            ref = self.reader.try_acquire_latest_frame()
            if ref is not None:
                with ref:
                    stamp = ref.system_relative_time
                    if stamp is None or stamp != self.last_frame_time:
                        self.last_frame_time = stamp
                        video = ref.video_media_frame
                        bitmap = video.software_bitmap if video is not None else None
                        if bitmap is not None:
                            with bitmap:
                                if bitmap.bitmap_pixel_format == BitmapPixelFormat.BGRA8:
                                    return True, self.read_bgra(bitmap)
                                with SoftwareBitmap.convert_with_alpha(
                                    bitmap, BitmapPixelFormat.BGRA8, BitmapAlphaMode.IGNORE
                                ) as bgra:
                                    return True, self.read_bgra(bgra)
            if monotonic() >= deadline or self.stop.wait(0.006):
                break
        return False, None

    def read_bgra(self, bitmap):
        width, height = bitmap.pixel_width, bitmap.pixel_height
        size = width * height * 4
        if self.buffer is None or self.buffer.capacity != size:
            self.buffer = Buffer(size)
            self.buffer.length = size
        bitmap.copy_to_buffer(self.buffer)
        pixels = np.frombuffer(self.buffer, dtype=np.uint8, count=size).reshape(height, width, 4)
        return self.cv2.cvtColor(pixels, self.cv2.COLOR_BGRA2BGR)

    def release(self):
        try:
            if self.zoom_change is not None:
                control, previous, widest = self.zoom_change
                with suppress(OSError, RuntimeError):
                    if control.value == widest:
                        control.value = previous
            if self.reader is not None:
                self.reader.close()
        finally:
            try:
                if self.capture is not None:
                    self.capture.close()
            finally:
                self.reader = self.capture = self.buffer = self.zoom_change = self.settings = None
                uninit_apartment()
