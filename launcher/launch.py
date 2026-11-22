"""Install the bundled app into a private environment and launch it."""

import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 10):
        raise RuntimeError("Install Python 3.10 or newer and reopen this launcher.")
    try:
        import tkinter
    except ImportError as exc:
        raise RuntimeError("This Python needs Tk. On Linux, install python3-tk (or python3-tkinter).") from exc

    folder = Path(__file__).resolve().parent
    wheel_dir = folder / "wheel"
    manifest = wheel_dir / "wheel-name.txt"
    wheel_name = manifest.read_text(encoding="utf-8").strip() if manifest.is_file() else ""
    if Path(wheel_name).name != wheel_name or not wheel_name.endswith(".whl"):
        raise RuntimeError("The app wheel is missing. Extract all files from the setup ZIP and try again.")
    wheel = wheel_dir / wheel_name
    if not wheel.is_file():
        raise RuntimeError("The app wheel is missing. Extract all files from the setup ZIP and try again.")
    environment = folder / ".venv"
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    receipt = environment / ".installed-wheel"
    if not python.is_file() or not receipt.is_file() or receipt.read_text() != wheel.name:
        print("Setting up MimedControl. The first launch may download dependencies.", flush=True)
        venv.EnvBuilder(with_pip=True, symlinks=os.name != "nt").create(environment)
        subprocess.check_call([str(python), "-c", "import tkinter"])
        subprocess.check_call([str(python), "-m", "pip", "--disable-pip-version-check", "--no-cache-dir",
                               "install", str(wheel)])
        receipt.write_text(wheel.name)

    options = [option for option in sys.argv[1:] if option in ("--camera-settings",)]
    if os.name == "nt":
        subprocess.Popen([str(bin_dir / "pythonw.exe"), "-m", "mimedcontrol", *options])
    else:
        os.execv(python, [str(python), "-m", "mimedcontrol", *options])


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"MimedControl setup could not complete. {exc}", file=sys.stderr)
        sys.exit(1)
