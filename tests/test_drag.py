"""Press, hold and drag with the click gesture."""
import numpy as np
import pytest
from hands import palm_pinch, palm_ready, pts, ready, shape, squeeze

from mimedcontrol.gestures import GestureState
from mimedcontrol.relaxed_hand import RELAXED_HAND

W, H = 640, 480


class Run:
    """Feed poses at 30 frames per second and keep what the recognizer reports."""

    def __init__(self, mode=RELAXED_HAND, number_pad=False, drag=True, grip=squeeze):
        self.g = GestureState(mode)
        self.g.drag_enabled = drag
        self.grip, self.number_pad = grip, number_pad
        self.t, self.log, self.center = 10.0, [], np.array((320.0, 240.0))

    def frame(self, hand, center=None):
        self.t += 1 / 30
        if center is not None:
            self.center = np.asarray(center, float)
        points = None if hand is None else pts(hand, tuple(self.center)).astype(np.float32)
        gesture = self.g.update(points, W, H, self.t, self.number_pad, camera_aim=True)
        self.log.append(gesture)
        return gesture

    def hold(self, hand, seconds):
        for _ in range(max(1, round(seconds * 30))):
            self.frame(hand)
        return self

    def move(self, hand, to, seconds):
        start, n = self.center.copy(), max(1, round(seconds * 30))
        for i in range(n):
            self.frame(hand, start + (np.asarray(to, float) - start) * (i + 1) / n)
        return self

    def close(self, frames=3):
        for depth in np.linspace(0.5, 1.0, frames):
            self.frame(self.grip(depth))
        return self

    def open(self, frames=3):
        for depth in np.linspace(0.8, 0.0, frames):
            self.frame(self.grip(depth))
        return self

    def count(self, field):
        return sum(bool(getattr(g, field)) for g in self.log)


def test_quick_squeeze_is_a_clean_click():
    r = Run().hold(ready(), 0.6)
    mark = len(r.log)
    r.close().hold(squeeze(), 0.12).open().hold(ready(), 0.4)
    after = r.log[mark:]
    assert r.count("press") == 1 and r.count("release") == 1
    assert not any(g.drag_motion for g in after)
    assert not any(g.click for g in after)  # the press and release are the click
    pressed = next(i for i, g in enumerate(after) if g.press)
    released = next(i for i, g in enumerate(after) if g.release)
    assert all(g.held for g in after[pressed:released])
    assert any(g.camera_pointer is not None for g in after[released:])  # aiming resumes


def test_hold_and_move_drags_one_to_one():
    r = Run().hold(ready(), 0.6).close().hold(squeeze(), 0.25)
    r.move(squeeze(), (440, 240), 0.8).hold(squeeze(), 0.2)
    moving = [g for g in r.log if g.drag_motion is not None]
    r.open().hold(ready(), 0.3)
    travel = 120 / (W - 1)
    assert r.count("press") == 1 and r.count("release") == 1
    dx, dy = moving[-1].drag_motion
    assert abs(dy) < 0.003
    assert travel - 0.03 <= dx <= travel  # the hand's travel, less a small start-up distance
    assert abs(moving[0].drag_motion[0]) < 0.012  # starts from where the button went down
    release = next(g for g in r.log if g.release)
    assert release.aim_held and release.drag_motion is None


def test_wobble_while_holding_still_stays_a_press():
    r = Run().hold(ready(), 0.6).close()
    for k in range(15):
        r.frame(squeeze(0.92 + 0.08 * (k % 2)), (320 + (k % 3) - 1, 240 + (k % 2)))
    assert r.count("press") == 1 and r.count("release") == 0
    assert not any(g.drag_motion for g in r.log)


def test_one_open_looking_frame_does_not_drop_a_drag():
    r = Run().hold(ready(), 0.6).close().hold(squeeze(), 0.2).move(squeeze(), (400, 240), 0.4)
    r.frame(squeeze(0.3))
    r.move(squeeze(), (460, 240), 0.3)
    assert r.count("release") == 0 and r.log[-1].drag_motion is not None
    r.open()
    assert r.count("release") == 1


def test_hand_briefly_out_of_view_keeps_the_button_down():
    r = Run().hold(ready(), 0.6).close().hold(squeeze(), 0.2).move(squeeze(), (400, 240), 0.4)
    r.hold(None, 5 / 30)
    assert r.count("release") == 0 and r.log[-1].held
    r.move(squeeze(), (430, 240), 0.3)
    assert r.count("release") == 0 and r.log[-1].drag_motion is not None
    r.hold(None, 0.4)
    assert r.count("release") == 1 and r.log[-1].pose == "lost"


def test_number_pad_squeeze_types_a_key():
    r = Run(number_pad=True).hold(ready(), 0.6).close().hold(squeeze(), 0.2).open().hold(ready(), 0.3)
    assert r.count("press") == 0 and r.count("click") == 1


def test_hold_to_drag_off_gives_instant_clicks():
    r = Run(drag=False).hold(ready(), 0.6).close().hold(squeeze(), 0.3).move(squeeze(), (420, 240), 0.4)
    r.open().hold(ready(), 0.3)
    assert r.count("press") == 0 and r.count("click") == 1
    assert not any(g.drag_motion for g in r.log)


def test_right_click_pose_still_right_clicks():
    r = Run().hold(ready(), 0.6).hold(shape((0.0, 1.0, 1.0, 1.0)), 4 / 30).hold(ready(), 0.3)
    assert r.count("right_click") == 1 and r.count("press") == 0


def test_press_again_after_a_drag():
    r = Run().hold(ready(), 0.6).close().hold(squeeze(), 0.2).move(squeeze(), (420, 240), 0.4).open()
    r.hold(ready(), 0.4).close().hold(squeeze(), 0.1).open().hold(ready(), 0.3)
    assert r.count("press") == 2 and r.count("release") == 2


@pytest.mark.parametrize("mode", ["Palm + pinch"])
def test_pinch_modes_drag_too(mode):
    r = Run(mode=mode, grip=palm_pinch).hold(palm_ready(), 0.8).close(4).hold(palm_pinch(), 0.25)
    r.move(palm_pinch(), (420, 240), 0.6).hold(palm_pinch(), 0.1)
    moving = [g for g in r.log if g.drag_motion is not None]
    r.open(4).hold(palm_ready(), 0.3)
    assert r.count("press") == 1 and r.count("release") == 1
    assert moving and moving[-1].drag_motion[0] > 0.1
