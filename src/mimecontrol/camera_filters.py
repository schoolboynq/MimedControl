"""Exposure correction for the current tracking frame."""

CAMERA_FILTERS = ("Auto", "Low light", "Backlit", "Low contrast", "Off")


class CameraFilter:
    def __init__(self, cv2):
        self.cv2 = cv2
        self.mode = None
        self.next_check = 0.0
        self.curve = None
        self.backlit = False

    def apply(self, frame, mode, now):
        if mode == "Off":
            self.mode = mode
            return frame
        if mode != self.mode or now >= self.next_check:
            self.adjust(frame, mode)
            self.mode = mode
            self.next_check = now + 0.20
        return self.cv2.LUT(frame, self.curve) if self.curve is not None else frame

    def adjust(self, frame, mode):
        import numpy as np

        height, width = frame.shape[:2]
        sample = frame[::max(1, height // 36), ::max(1, width // 48)]
        gray = self.cv2.cvtColor(sample, self.cv2.COLOR_BGR2GRAY)
        counts = np.bincount(gray.ravel(), minlength=256).cumsum()
        low, shadow, upper, bright = counts.searchsorted(gray.size * np.array([0.01, 0.10, 0.75, 0.98]))
        # Use separate entry/exit thresholds so normal exposure fluctuations don't
        # repeatedly switch shadow correction on and off.
        holding = self.mode == mode and self.backlit
        self.backlit = mode == "Backlit" or (
            mode == "Auto" and shadow < (42 if holding else 32) and upper > (200 if holding else 216)
        )
        self.curve = None
        if self.backlit:
            pivot = min(78, max(26, round(shadow * 1.6)))
            anchors = [0, pivot, 205, 255]
            output = [0, max(112, pivot + 55), 219, 255]
        elif mode == "Low light" or (mode == "Auto" and bright < 145):
            # Stretch dim detail with bounded gain, leaving headroom for highlights.
            pivot = max(1, min(bright, 210))
            level = 194 if mode == "Low light" else min(162, 290 - pivot)
            target = min(5 * pivot, max(pivot, level))
            if target <= pivot:
                return
            anchors, output = [0, pivot, 255], [0, target, 255]
        else:
            # Expand a narrow band of tones around its midpoint. A near-uniform
            # frame has too little detail to justify amplifying its noise.
            low, high = max(1, low), min(254, bright)
            span = high - low
            target = 108 if mode == "Low contrast" else 60
            if not 6 <= span < target:
                return
            gain = min(4, target / span)
            center = (low + high) / 2
            # Carry the contrast boost past the sampled range. Distant fingers
            # can occupy less than one percent of the frame.
            midpoint = min(234, max(21, center))
            left = max(1, center + (16 - midpoint) / gain)
            right = min(254, center + (239 - midpoint) / gain)
            anchors = [0, left, right, 255]
            output = [0, midpoint + (left - center) * gain, midpoint + (right - center) * gain, 255]
        self.curve = np.rint(np.interp(np.arange(256), anchors, output)).astype(np.uint8)
