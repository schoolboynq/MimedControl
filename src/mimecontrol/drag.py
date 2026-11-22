"""Press and hold with the click gesture.

Closing the click gesture presses the mouse button and opening it lets go, the way a real mouse button
works. A quick squeeze is an ordinary click on the spot you aimed at. Keep holding and move your hand to
drag: select text, move a file or a window, or pull a slider.
"""

from math import hypot

from .aim import PointerFilter

# Hand travel, as a share of the screen, that turns a held press into a drag. Closing or opening a grip
# shifts the hand a little, so a short squeeze stays a clean click where you aimed.
DRAG_START = 0.022
# After holding still for a moment, you clearly mean to hold, so less travel starts the drag.
HELD_DRAG_START = 0.010
HOLD_STILL = 0.40
# A grip keeps settling for a few frames after it closes. That shift isn't a drag.
SETTLE = 0.12
# A drag survives the hand vanishing from view this long, so one missed frame doesn't drop what you carry.
LOST_GRACE = 0.30


class DragTracker:
    """Follow the hand while the button is held: frozen at first, then moving with the hand."""

    def __init__(self):
        self.filter = PointerFilter(elastic=True)
        self.reset()

    def reset(self):
        self.state = None
        self.pressed_at = self.seen_at = None
        self.anchor = self.origin = self.hand = None
        self.open_frames = 0

    @property
    def active(self):
        return self.state is not None

    @property
    def dragging(self):
        return self.state == "dragging"

    def press(self, target, now, response):
        self.filter.reset()
        self.hand = self.anchor = self.filter.update(*target, now, response)
        self.state = "pressed"
        self.pressed_at = self.seen_at = now
        self.open_frames = 0

    def update(self, contact, target, now, response, release_frames=2):
        """Return (release, motion): motion is the hand's travel since the drag began, or None while the
        pointer should stay where the button went down."""
        self.seen_at = now
        if target is not None:
            self.hand = self.filter.update(*target, now, response)
        if not contact:
            # Freeze while the grip may be opening, so the button comes up where it was.
            self.open_frames += 1
            return self.open_frames >= release_frames, None
        self.open_frames = 0
        if self.state == "pressed":
            travel = hypot(self.hand[0] - self.anchor[0], self.hand[1] - self.anchor[1])
            held = now - self.pressed_at
            if held < SETTLE and travel < DRAG_START:
                self.anchor = self.hand
                return False, None
            if travel < (HELD_DRAG_START if held >= HOLD_STILL else DRAG_START):
                return False, None
            # Move from here on, one to one with the hand, so fine drags stay precise.
            self.state, self.origin = "dragging", self.hand
        return False, (self.hand[0] - self.origin[0], self.hand[1] - self.origin[1])
