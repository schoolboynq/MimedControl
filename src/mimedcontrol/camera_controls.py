"""Read and change a webcam's own picture settings, the standard ones a camera maker's app changes.

Windows exposes each setting as a property of the camera's driver (kernel streaming Video Proc Amp
and Camera Control). A change goes to the camera itself, so it stays for every app until you change
it again. Vendor extras such as HDR or noise reduction use private commands and aren't here.
"""

import struct
import uuid
from dataclasses import dataclass, replace

PROCAMP = uuid.UUID("C6E13360-30AC-11D0-A18C-00A0C9118956")  # PROPSETID_VIDCAP_VIDEOPROCAMP
CAMERA = uuid.UUID("C6E13370-30AC-11D0-A18C-00A0C9118956")   # PROPSETID_VIDCAP_CAMERACONTROL
GET, SET, BASIC_SUPPORT, DEFAULT_VALUES = 0x1, 0x2, 0x200, 0x10000
AUTO, MANUAL = 0x1, 0x2
REQUEST_SIZE = 40
MEMBER_RANGES = 0x1

# key, property set, property id, label. The order is the order on screen.
CONTROLS = (
    ("brightness", PROCAMP, 0, "Brightness"),
    ("contrast", PROCAMP, 1, "Contrast"),
    ("saturation", PROCAMP, 3, "Saturation"),
    ("sharpness", PROCAMP, 4, "Sharpness"),
    ("hue", PROCAMP, 2, "Hue"),
    ("gamma", PROCAMP, 5, "Gamma"),
    ("gain", PROCAMP, 9, "Gain"),
    ("backlight", PROCAMP, 8, "Backlight compensation"),
    ("white_balance", PROCAMP, 7, "White balance"),
    ("exposure", CAMERA, 4, "Exposure"),
    ("focus", CAMERA, 6, "Focus"),
    ("zoom", CAMERA, 3, "Zoom"),
    ("powerline", PROCAMP, 13, "Anti-flicker"),
)
POWERLINE = "powerline"
POWERLINE_CHOICES = ((0, "Off"), (1, "50 Hz"), (2, "60 Hz"))
KEYS = tuple(key for key, *_ in CONTROLS)


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    minimum: int
    maximum: int
    step: int
    default: int
    value: int
    auto: bool
    can_auto: bool
    can_manual: bool = True

    def clamp(self, value):
        value = min(self.maximum, max(self.minimum, int(round(value))))
        return min(self.maximum, self.minimum + round((value - self.minimum) / self.step) * self.step)

    def describe(self, value=None):
        """A value in words: 1/32 s, 6500 K, 1.5×."""
        value = self.value if value is None else value
        if self.key == "exposure":
            return f"1/{2 ** -value:g} s" if value < 0 else f"{2 ** value:g} s"
        if self.key == "white_balance":
            return f"{value} K"
        if self.key == "zoom" and self.minimum > 0:
            return f"{value / self.minimum:.1f}×" + (" widest" if value == self.minimum else "")
        if self.key == POWERLINE:
            return dict(POWERLINE_CHOICES).get(value, str(value))
        return str(value)


def request(property_set, ident, flags, value=0, value_flags=0):
    """A KSPROPERTY header followed by the value, its flags, capabilities and padding: 40 bytes."""
    return property_set.bytes_le + struct.pack("<IIiIII", ident, flags, value, value_flags, 0, 0)


class CameraControls:
    """The settings of one open camera. transport reads and writes raw driver properties."""

    def __init__(self, transport):
        self.transport = transport
        self.controls = {}

    def read_all(self):
        self.controls = {}
        for key, property_set, ident, label in CONTROLS:
            control = self.read_control(key, property_set, ident, label)
            if control is not None:
                self.controls[key] = control
        return self.snapshot()

    def snapshot(self):
        return tuple(self.controls[key] for key in KEYS if key in self.controls)

    def read_control(self, key, property_set, ident, label):
        current = self.transport.get(request(property_set, ident, GET), REQUEST_SIZE)
        if current is None or len(current) < 36:
            return None
        value, flags, capabilities = struct.unpack_from("<iII", current, 24)
        basic = self.transport.get(request(property_set, ident, BASIC_SUPPORT), 72)
        if basic is None or len(basic) < 72:
            return None
        members, _size, count, _flags, step, _reserved, minimum, maximum = struct.unpack_from("<IIIIIIii", basic, 40)
        if not members & MEMBER_RANGES or count < 1 or minimum >= maximum:
            return None
        defaults = self.transport.get(request(property_set, ident, DEFAULT_VALUES), 60)
        default = struct.unpack_from("<i", defaults, 56)[0] if defaults is not None and len(defaults) >= 60 else value
        can_auto, can_manual = bool(capabilities & AUTO), bool(capabilities & MANUAL)
        if not can_manual and not can_auto:
            can_manual = True  # Anti-flicker reports no auto or manual flags, and other drivers do the same.
        if key == POWERLINE and not (minimum <= value <= maximum and value in dict(POWERLINE_CHOICES)):
            return None
        return Control(key, label, minimum, maximum, max(1, step), min(maximum, max(minimum, default)),
                       value, bool(flags & AUTO) and can_auto, can_auto, can_manual)

    def refresh(self, key):
        """Read one setting again, such as an automatic exposure that keeps changing."""
        control = self.controls.get(key)
        if control is None:
            return None
        property_set, ident = next((s, i) for k, s, i, _ in CONTROLS if k == key)
        current = self.transport.get(request(property_set, ident, GET), REQUEST_SIZE)
        if current is not None and len(current) >= 36:
            value, flags, _ = struct.unpack_from("<iII", current, 24)
            self.controls[key] = replace(control, value=value, auto=bool(flags & AUTO) and control.can_auto)
        return self.controls[key]

    def write(self, key, value=None, auto=None):
        """Change one setting. Returns the setting as the camera now reports it, or None if it refused."""
        control = self.controls.get(key)
        if control is None:
            return None
        auto = control.auto if auto is None else bool(auto) and control.can_auto
        value = control.value if value is None else control.clamp(value)
        if key == POWERLINE:
            # Windows has a supported call for anti-flicker, so use it rather than a raw property write.
            ok = value in dict(POWERLINE_CHOICES) and self.transport.set_powerline(value)
        else:
            if not auto and not control.can_manual:
                return None
            property_set, ident = next((s, i) for k, s, i, _ in CONTROLS if k == key)
            data = request(property_set, ident, SET, value, AUTO if auto else MANUAL)
            ok = self.transport.set(data, data)
        return self.refresh(key) if ok else None

    def apply(self, saved):
        """Apply saved settings: {key: {"value": n} or {"auto": True}}. Returns the keys that took."""
        applied = []
        for key in KEYS:
            choice = saved.get(key) if isinstance(saved, dict) else None
            if not isinstance(choice, dict) or key not in self.controls:
                continue
            if choice.get("auto") is True:
                result = self.write(key, auto=True)
            elif isinstance(choice.get("value"), (int, float)) and not isinstance(choice.get("value"), bool):
                result = self.write(key, value=choice["value"], auto=False)
            else:
                continue
            if result is not None:
                applied.append(key)
        return applied


class WinrtTransport:
    """Driver properties through Windows.Media.Devices.VideoDeviceController."""

    def __init__(self, controller):
        self.controller = controller

    def get(self, data, size):
        result = self.controller.get_device_property_by_extended_id(data, size)
        if getattr(result.status, "name", "") != "SUCCESS" or result.value is None:
            return None
        from winrt.windows.foundation import IPropertyValue

        return bytes(result.value.as_(IPropertyValue).get_uint8_array())

    def set(self, data, value):
        return getattr(self.controller.set_device_property_by_extended_id(data, value), "name", "") == "SUCCESS"

    def set_powerline(self, value):
        from winrt.windows.media.capture import PowerlineFrequency

        return bool(self.controller.try_set_powerline_frequency(PowerlineFrequency(value)))


def clean_saved(data):
    """Saved camera settings from settings.json: {camera name: {key: {"value": n} | {"auto": True}}}."""
    cleaned = {}
    if not isinstance(data, dict):
        return cleaned
    for camera, choices in data.items():
        if not isinstance(camera, str) or not isinstance(choices, dict):
            continue
        kept = {}
        for key, choice in choices.items():
            if key not in KEYS or not isinstance(choice, dict):
                continue
            if choice.get("auto") is True:
                kept[key] = {"auto": True}
            elif isinstance(choice.get("value"), (int, float)) and not isinstance(choice.get("value"), bool):
                kept[key] = {"value": int(choice["value"])}
        if kept:
            cleaned[camera] = kept
    return cleaned
