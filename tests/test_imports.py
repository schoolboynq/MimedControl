"""Every module imports on this platform."""
import importlib
import pkgutil
import sys

import mimecontrol

WINDOWS_ONLY = {"targets_windows", "windows_camera", "monitors_windows", "camera_controls"}


def test_modules_import():
    for module in pkgutil.iter_modules(mimecontrol.__path__):
        if module.name == "__main__" or (module.name in WINDOWS_ONLY and sys.platform != "win32"):
            continue
        importlib.import_module(f"mimecontrol.{module.name}")
