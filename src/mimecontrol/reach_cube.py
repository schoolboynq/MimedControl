import tkinter as tk

from .calibration import FACE_DEPTH
from . import theme

FACE = "#3b404c"
ACTIVE_FACE = "#d8d1c4"
DRAWN = theme.ACCENT
HANDS = {"left": "#67dafa", "right": "#c9a7ff"}


class ReachCube(tk.Canvas):
    """Show the box you're drawing: its faces, the edges done so far, both hands and their targets.

    The front of the box is close in to you, and the back is where your arms reach
    out toward the screen. A depth gauge on the right shows both hands' distance.
    """

    def __init__(self, parent, scale=1.0):
        self.k = k = scale
        super().__init__(parent, width=round(380 * k), height=round(235 * k), bg=theme.DARK, highlightthickness=0)
        small = theme.font(max(9, round(9 * k)))
        self.faces = {}
        for name, depth in sorted(FACE_DEPTH.items(), key=lambda item: -item[1]):
            face = [self.project(x, y, depth) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
            self.faces[name] = self.create_line(*face, fill=FACE, width=2, dash=() if depth != 0.5 else (5, 3))
        for x, y in ((0, 0), (0, 1), (1, 0), (1, 1)):
            self.create_line(*self.project(x, y, 0), *self.project(x, y, 1), fill="#2a2e38", dash=(4, 3))
        self.create_text(*self.at(30, 224), text="Close in", anchor="w", fill=theme.INK, font=small)
        self.create_text(*self.at(94, 40), text="Arms out", anchor="e", fill=theme.INK, font=small)
        self.create_line(*self.at(352, 192), *self.at(352, 62), fill="#5d6270", width=2)
        self.create_text(*self.at(352, 48), text="Out", fill=theme.MUTED, font=small)
        self.create_text(*self.at(352, 207), text="In", fill=theme.MUTED, font=small)
        for column, (text, color) in enumerate((("Left hand", HANDS["left"]), ("Right hand", HANDS["right"]))):
            x = 14 + column * 96
            self.create_oval(*self.at(x, 9), *self.at(x + 12, 21), outline=color, width=2)
            self.create_text(*self.at(x + 18, 15), text=text, anchor="w", fill="white", font=small)
        self.lines = []
        self.trail_lines = {side: self.create_line(0, 0, 0, 0, fill=color, width=2, state="hidden")
                            for side, color in HANDS.items()}
        # Markers stay hidden until the first frame places them.
        self.depth_target = self.create_line(0, 0, 0, 0, fill=DRAWN, width=4, state="hidden")
        self.depth_hands = {side: self.create_oval(0, 0, 0, 0, fill=color, outline="", state="hidden")
                            for side, color in HANDS.items()}
        self.targets = [self.create_oval(0, 0, 0, 0, fill=DRAWN, outline="#472e09", width=2, state="hidden")
                        for _ in range(2)]
        self.progress = [self.create_arc(0, 0, 0, 0, start=90, style="arc", outline="#ffd38a", width=3,
                                         state="hidden") for _ in range(2)]
        self.hands = {side: self.create_oval(0, 0, 0, 0, outline=color, width=3, state="hidden")
                      for side, color in HANDS.items()}

    def at(self, x, y):
        return x * self.k, y * self.k

    def project(self, x, y, z):
        # Depth 0 is the front face, close in to you; depth 1 is arms out, at the back.
        return self.at(30 + z * 70 + x * (226 - z * 38), 70 - z * 36 + y * (138 - z * 32))

    def reset(self):
        for line in self.lines:
            self.delete(line)
        self.lines = []
        for item in (self.depth_target, *self.targets, *self.progress, *self.hands.values(),
                     *self.depth_hands.values(), *self.trail_lines.values()):
            self.itemconfigure(item, state="hidden")
        for item in self.faces.values():
            self.itemconfigure(item, fill=FACE)

    def draw(self, capture):
        active = capture.active_face
        for name, item in self.faces.items():
            self.itemconfigure(item, fill=ACTIVE_FACE if name == active else FACE)
        segments = capture.segments()
        if len(segments) != len(self.lines):
            self.reset()
            for start, end in segments:
                self.lines.append(self.create_line(*self.project(*start), *self.project(*end), fill=DRAWN, width=3))
            for line in self.lines:
                self.tag_lower(line, self.targets[0])
        for side, line in self.trail_lines.items():
            trail = capture.trails[side]
            if len(trail) > 1:
                self.coords(line, *(value for point in trail for value in self.project(*point)))
                self.itemconfigure(line, state="normal")
            else:
                self.itemconfigure(line, state="hidden")
        radius = 7 * self.k
        for target, arc, (x, y, z) in zip(self.targets, self.progress, capture.targets):
            px, py = self.project(x, y, z)
            self.coords(target, px - radius, py - radius, px + radius, py + radius)
            self.coords(arc, px - 2.3 * radius, py - 2.3 * radius, px + 2.3 * radius, py + 2.3 * radius)
            self.itemconfigure(arc, extent=-359 * capture.progress, state="normal")
            self.itemconfigure(target, state="normal")
        depth = 192 - 130 * capture.targets[0][2]
        self.coords(self.depth_target, *self.at(345, depth), *self.at(359, depth))
        self.itemconfigure(self.depth_target, state="normal")
        for side, ring in self.hands.items():
            hand = capture.hands[side]
            state = "normal" if hand is not None else "hidden"
            self.itemconfigure(ring, state=state)
            self.itemconfigure(self.depth_hands[side], state=state)
            if hand is None:
                continue
            px, py = self.project(*hand)
            size = 11 * self.k
            self.coords(ring, px - size, py - size, px + size, py + size)
            level = 192 - 130 * min(1.2, max(-0.2, hand[2]))
            offset = -4 if side == "left" else 4
            self.coords(self.depth_hands[side], *self.at(352 + offset - 3, level - 3), *self.at(352 + offset + 3, level + 3))
