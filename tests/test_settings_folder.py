"""The settings folder moves with the app's name without losing earlier calibrations."""

import sys

from mimedcontrol import calibration


def use_root(monkeypatch, root):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))


def test_new_folder_by_default(monkeypatch, tmp_path):
    use_root(monkeypatch, tmp_path)
    assert calibration.profile_path(1, 2) == tmp_path / "MimedControl" / "reach-1-2.json"
    assert not (tmp_path / "MimedControl").exists()


def test_earlier_folder_is_copied_once(monkeypatch, tmp_path):
    use_root(monkeypatch, tmp_path)
    earlier = tmp_path / "MimeControl"
    earlier.mkdir()
    (earlier / "settings.json").write_text('{"sticky": true}')
    (earlier / "reach-0-0.json").write_text("{}")

    folder = calibration.settings_folder()

    assert folder == tmp_path / "MimedControl"
    assert (folder / "settings.json").read_text() == '{"sticky": true}'
    assert (folder / "reach-0-0.json").exists()
    assert (earlier / "settings.json").exists(), "the earlier folder is left in place"
    assert not (tmp_path / "MimedControl.partial").exists()

    (folder / "settings.json").write_text('{"sticky": false}')
    calibration.settings_folder()
    assert (folder / "settings.json").read_text() == '{"sticky": false}', "copied only the first time"
