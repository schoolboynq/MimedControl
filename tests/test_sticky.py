"""Sticky targets: friction over controls, no jumps, catch-up off controls, reachable edges."""
from math import hypot, pi, sin

import pytest

from mimedcontrol.displays import DisplayLayout
from mimedcontrol.sticky import StickyPointer
from mimedcontrol.targets_windows import TargetSnapshot

LAYOUT = DisplayLayout(((0, 0, 1920, 1080),))
MONITOR = LAYOUT.rects[0]
BUTTON = (900, 500, 980, 530)  # 80 x 30
FIELD = (600, 300, 1200, 330)  # a 600 x 30 text field


class Targets:
    """Controls at fixed rectangles. A lookup finds those near the probe, as the UI Automation search does."""

    def __init__(self, *rects, flaky=False):
        self.rects, self.flaky, self.calls = rects, flaky, 0

    def lookup(self, x, y, radius):
        self.calls += 1
        if self.flaky and self.calls % 3 == 0:
            return TargetSnapshot((), (1, 1), pending=True)
        found = tuple(r for r in self.rects if r[0] - radius <= x < r[2] + radius and r[1] - radius <= y < r[3] + radius)
        return TargetSnapshot(found, (1, 1))


def run(path, targets, sticky=None, fps=60):
    sticky = sticky or StickyPointer(96)
    out, t = [], 50.0
    for x, y in path:
        t += 1 / fps
        pointer = sticky.update(LAYOUT.to_pointer(round(x), round(y)), LAYOUT, t, targets, monitor=MONITOR)
        out.append(LAYOUT.to_pixel(pointer))
    return out


def line(a, b, seconds, fps=60):
    n = max(1, round(seconds * fps))
    return [(a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n) for i in range(n)]


@pytest.fixture
def targets():
    return Targets(BUTTON, FIELD)


def test_without_controls_the_pointer_is_the_hand():
    path = line((100, 100), (1500, 900), 1.0)
    assert run(path, None) == [(round(x), round(y)) for x, y in path]


def test_slow_pass_slows_on_the_button_without_jumping(targets):
    path = [(800, 515)] + line((800, 515), (1080, 515), 2.0)  # 140 px/s
    out = run(path, targets)
    hand_step = 280 / 120
    steps = [hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(out, out[1:])]
    assert max(steps) <= hand_step * 2.2
    entering = [b[0] - a[0] for a, b in zip(out, out[1:]) if BUTTON[0] + 5 <= a[0] < BUTTON[0] + 30]
    assert sum(entering) / len(entering) <= 0.5 * hand_step
    assert sum(BUTTON[0] <= o[0] < BUTTON[2] for o in out) / 60 >= 1.5 * 80 / 140
    assert out[-1][0] >= BUTTON[2]  # leaves once the hand moves on
    out = run(path + line((1080, 515), (1300, 515), 1.6), targets)
    assert abs(out[-1][0] - 1300) <= 12  # then catches up


def test_fast_sweep_is_not_slowed(targets):
    path = [(400, 515)] + line((400, 515), (1500, 515), 0.4)
    assert max(abs(o[0] - round(x)) for o, (x, _) in zip(run(path, targets), path)) <= 2


def test_tremor_is_absorbed_and_never_creeps(targets):
    still = [(940, 515)] * 30
    tremor = [(940 + 2.5 * sin(2 * pi * 9 * k / 60), 515 + 1.5 * sin(2 * pi * 7 * k / 60)) for k in range(90)]
    out = run(still + tremor, targets)
    assert max(o[0] for o in out[30:]) - min(o[0] for o in out[30:]) <= 3
    jitter = [(940 + 3 * (-1) ** k, 515 + 2 * (-1) ** (k // 2)) for k in range(120)]
    out = run(still + jitter, targets)
    assert abs(sum(o[0] for o in out[-30:]) / 30 - 940) <= 3


def test_wide_fields_only_slow_across_their_short_side(targets):
    out = run([(700, 315)] + line((700, 315), (1000, 315), 2.0), targets)
    assert out[-1][0] >= 940
    out = run([(900, 315)] + line((900, 315), (900, 345), 1.0), targets)
    steps = [b[1] - a[1] for a, b in zip(out, out[1:]) if a[1] < FIELD[3] - 2]
    assert sum(steps) / len(steps) <= 0.6 * 30 / 60


def test_lag_is_capped(targets):
    path = [(905, 515)] + line((905, 515), (906, 515), 0.1) + line((906, 515), (1300, 515), 6.0)
    assert max(abs(o[0] - round(x)) for o, (x, _) in zip(run(path, targets), path)) <= 121


def test_edges_stay_reachable():
    out = run([(1800, 520)] + line((1800, 520), (1919, 520), 1.5), Targets((1880, 500, 1920, 540)))
    assert out[-1][0] == 1919


def test_place_carries_on_from_a_drop_without_jumping():
    sticky = StickyPointer(96)
    run([(500, 500)] * 5, None, sticky=sticky)
    sticky.place(LAYOUT.to_pointer(700, 520))
    out = run([(520, 500)] + line((520, 500), (900, 500), 1.5), None, sticky=sticky)
    assert out[0] == (700, 520)
    assert max(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(out, out[1:])) <= 12
    assert abs(out[-1][0] - 900) <= 10 and abs(out[-1][1] - 500) <= 5


def test_pending_lookups_keep_the_same_friction():
    path = [(800, 515)] + line((800, 515), (1080, 515), 2.0)
    steady = run(path, Targets(BUTTON, FIELD))
    flaky = run(path, Targets(BUTTON, FIELD, flaky=True))
    assert max(abs(a[0] - b[0]) for a, b in zip(steady, flaky)) <= 4
