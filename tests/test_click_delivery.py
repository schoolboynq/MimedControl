"""Mouse button timing: quick clicks keep their spot, held buttons drag, nothing stays stuck."""
import pytest

from mimecontrol import desktop


@pytest.fixture
def clicks(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(desktop, "monotonic", lambda: clock[0])
    events, spot = [], [(0.0, 0.0)]

    def set_button(button, down):
        events.append(("down" if down else "up", button, spot[0]))

    def move(pointer):
        spot[0] = pointer
        events.append(("move", pointer))

    delivery = desktop.ClickDelivery(set_button, move, lambda: spot[0])
    delivery.events, delivery.clock = events, clock

    def tick(seconds, steps=1):
        for _ in range(steps):
            clock[0] += seconds / steps
            delivery.advance()
    delivery.tick = tick
    return delivery


def test_hold_stays_down_and_drags(clicks):
    clicks.hold("left", target=(0.2, 0.2))
    clicks.tick(0.5, steps=5)
    assert clicks.events == [("move", (0.2, 0.2)), ("down", "left", (0.2, 0.2))]
    clicks.move((0.3, 0.25))
    assert clicks.events[-1] == ("move", (0.3, 0.25))  # moves pass straight through
    clicks.release_hold(target=(0.3, 0.25))
    assert clicks.events[-1] == ("up", "left", (0.3, 0.25)) and not clicks.held


def test_hold_waits_for_a_click_and_becomes_a_click_if_let_go_first(clicks):
    clicks.click("left", target=(0.5, 0.5))
    clicks.hold("left", target=(0.6, 0.6))
    clicks.move((0.55, 0.5))
    assert clicks.events[-1] == ("down", "left", (0.5, 0.5))  # moves wait while a quick click is down
    clicks.release_hold(target=(0.6, 0.6))
    clicks.tick(0.3, steps=10)
    assert [e[0] for e in clicks.events if e[0] != "move"] == ["down", "up", "down", "up"]
    assert not clicks.held and clicks.pressed_button is None


def test_cancel_releases_a_held_button(clicks):
    clicks.hold("left", target=(0.1, 0.1))
    clicks.tick(5)
    clicks.cancel()
    assert clicks.events[-1][0] == "up" and clicks.pressed_button is None and not clicks.held
    clicks.release_hold()
    assert [e[0] for e in clicks.events].count("up") == 1  # a late release sends nothing
