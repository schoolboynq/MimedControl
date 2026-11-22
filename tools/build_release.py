"""Build the wheel and MimeControl-Setup.zip (launchers plus the bundled wheel) into release/."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"


def main():
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    subprocess.check_call([sys.executable, "-m", "build", "--wheel", "--outdir", str(ROOT / "dist"), str(ROOT)])
    wheel = next((ROOT / "dist").glob("mimecontrol-*-py3-none-any.whl"))
    RELEASE.mkdir(exist_ok=True)
    archive_path = RELEASE / "MimeControl-Setup.zip"
    files = [(ROOT / "launcher" / name, name) for name in
             ("launch.py", "Launch MimeControl.cmd", "Launch MimeControl.command", "Camera Settings.cmd")]
    files += [(ROOT / "docs" / "GUIDE.md", "START HERE.md"), (ROOT / "LICENSE", "LICENSE"), (ROOT / "NOTICE", "NOTICE"),
              (wheel, f"wheel/{wheel.name}")]
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ("MimeControl/", "MimeControl/wheel/"):
            entry = zipfile.ZipInfo(folder)
            entry.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(entry, b"")
        for source, name in files:
            entry = zipfile.ZipInfo(f"MimeControl/{name}", date_time=(2026, 11, 21, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (0o100755 if name.endswith(".command") else 0o100644) << 16
            archive.writestr(entry, source.read_bytes())
        entry = zipfile.ZipInfo("MimeControl/wheel/wheel-name.txt", date_time=(2026, 11, 21, 0, 0, 0))
        archive.writestr(entry, wheel.name)
    print(archive_path, archive_path.stat().st_size)


if __name__ == "__main__":
    main()
