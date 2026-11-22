"""A pop-up that walks you through calibration: the step you're on, and the next three.

Every part of the panel has a fixed size, so neither it nor the calibration view moves
when the words change. It sits beside the main window and stays on top while you calibrate.
"""

import tkinter as tk
from tkinter import font as tkfont

from .calibration import FACE_DEPTH, MARKS, PHASE_TARGETS, step_detail, step_title
from . import theme

DARK = theme.BG
TEXT = theme.INK
MUTED = theme.MUTED
GOLD = theme.ACCENT
TRACK = theme.FIELD
FACE = "#4b505d"
WIDTH = 440  # Content width at 96 dpi.
# Fixed text areas, in lines. The longest wording of each part fits; see test_steps_panel.
LINES = {"kicker": 1, "headline": 2, "body": 5, "feedback": 3, "next_title": 1, "next_detail": 1}
FONTS = {"kicker": 11, "headline": 21, "body": 16, "feedback": 15, "next_title": 13, "next_detail": 11}
BOLD = ("kicker", "headline", "feedback", "next_title")
PREVIOUS = {"spread": ("top", "top"), "down": ("top_left", "top_right"), "bottom": ("bottom_left", "bottom_right")}


class StepsPanel:
    def __init__(self, root, on_move=None):
        self.root = root
        self.on_move = on_move
        self.k = k = max(1.0, root.winfo_fpixels("1i") / 96)
        self.width = width = round(WIDTH * k)
        self.position = None
        self.shown = False
        self.window = tk.Toplevel(root, bg=DARK)
        self.window.withdraw()
        self.window.title("Calibration steps")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        self.window.bind("<Configure>", self.moved)
        family = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
        self.fonts = {name: tkfont.Font(root, family=family, size=size, weight="bold" if name in BOLD else "normal")
                      for name, size in FONTS.items()}
        body = tk.Frame(self.window, bg=DARK, padx=round(20 * k), pady=round(16 * k))
        body.pack()
        self.texts = {}
        self.text("kicker", body, width, GOLD).pack(anchor="w")
        top = tk.Frame(body, bg=DARK)
        top.pack(anchor="w", pady=(round(6 * k), round(8 * k)))
        self.icon = tk.Canvas(top, width=round(96 * k), height=round(66 * k), bg=DARK, highlightthickness=0)
        self.icon.pack(side="left", padx=(0, round(12 * k)))
        self.text("headline", top, width - round(108 * k), TEXT).pack(side="left")
        self.text("body", body, width, TEXT).pack(anchor="w")
        self.text("feedback", body, width, GOLD).pack(anchor="w", pady=(round(8 * k), 0))
        self.bar = tk.Canvas(body, width=width, height=round(10 * k), bg=DARK, highlightthickness=0)
        self.bar.pack(anchor="w", pady=(round(6 * k), round(14 * k)))
        self.bar.create_rectangle(0, 0, width, round(10 * k), fill=TRACK, width=0)
        self.fill = self.bar.create_rectangle(0, 0, 0, round(10 * k), fill=GOLD, width=0)
        tk.Frame(body, bg=TRACK, width=width, height=max(1, round(k))).pack(anchor="w")
        self.list_title = tk.Label(body, text="NEXT", bg=DARK, fg=MUTED, font=self.fonts["kicker"], bd=0, padx=0,
                                   pady=0)
        self.list_title.pack(anchor="w", pady=(round(10 * k), round(4 * k)))
        self.rows = []
        for _ in range(3):
            row = tk.Frame(body, bg=DARK)
            row.pack(anchor="w", pady=round(4 * k))
            icon = tk.Canvas(row, width=round(56 * k), height=round(40 * k), bg=DARK, highlightthickness=0)
            icon.pack(side="left", padx=(0, round(12 * k)))
            words = tk.Frame(row, bg=DARK)
            words.pack(side="left")
            title = self.text("next_title", words, width - round(68 * k), TEXT, keep=False)
            title.pack(anchor="w")
            detail = self.text("next_detail", words, width - round(68 * k), MUTED, keep=False)
            detail.pack(anchor="w")
            self.rows.append((icon, title.label, detail.label))
        self.view = {}
        self.icons = [None] * 4

    def text(self, name, parent, width, color, keep=True):
        """A label inside a frame of fixed size, so changing its words can't move anything."""
        font = self.fonts[name]
        frame = tk.Frame(parent, width=width, height=font.metrics("linespace") * LINES[name], bg=DARK)
        frame.pack_propagate(False)
        frame.label = tk.Label(frame, font=font, fg=color, bg=DARK, justify="left", anchor="nw",
                               wraplength=width, bd=0, padx=0, pady=0)
        frame.label.pack(fill="both", expand=True)
        if keep:
            self.texts[name] = frame.label
        return frame

    # Showing and placing
    def show(self, beside, monitors=()):
        if self.position is None or not any(left <= self.position[0] < right and top <= self.position[1] < bottom
                                            for left, top, right, bottom in monitors or self.screen()):
            self.position = self.place_beside(beside, monitors or self.screen())
        self.window.geometry(f"+{self.position[0]}+{self.position[1]}")
        self.window.deiconify()
        self.window.attributes("-topmost", True)
        self.window.lift()
        self.shown = True

    def hide(self):
        self.shown = False
        self.window.withdraw()

    def screen(self):
        return ((0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()),)

    def place_beside(self, beside, monitors):
        """Right of the main window if it fits on that screen, else left of it, else at the screen's edge."""
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        left, top = beside.winfo_x(), beside.winfo_y()
        right = beside.winfo_rootx() + beside.winfo_width()
        center = (left + right) // 2, top + beside.winfo_height() // 2
        screen = next((m for m in monitors if m[0] <= center[0] < m[2] and m[1] <= center[1] < m[3]), monitors[0])
        gap = round(14 * self.k)
        if right + gap + width <= screen[2]:
            x = right + gap
        elif left - gap - width >= screen[0]:
            x = left - gap - width
        else:
            x = max(screen[0], screen[2] - width - gap)
        # Leave room for the title bar, which Tk doesn't count in the window's size.
        y = max(screen[1], min(top, screen[3] - height - round(48 * self.k)))
        return x, y

    def moved(self, event):
        if event.widget is self.window and self.shown and self.window.winfo_ismapped():
            position = self.window.winfo_x(), self.window.winfo_y()
            if position != self.position:
                self.position = position
                if self.on_move is not None:
                    self.on_move(position)

    # Content
    def update(self, kicker, headline, body, feedback, progress, current, upcoming, rows=None):
        """rows, when given, replaces the next steps with (icon step, title, detail), as after refining."""
        view = {"kicker": kicker.upper(), "headline": headline, "body": body, "feedback": feedback}
        for name, text in view.items():
            if self.view.get(name) != text:
                self.view[name] = text
                self.fit(name, text)
        progress = min(1.0, max(0.0, float(progress or 0.0)))
        if self.view.get("progress") != progress:
            self.view["progress"] = progress
            self.bar.coords(self.fill, 0, 0, round(self.width * progress), round(10 * self.k))
        title = "NEXT" if rows is None else "MOVED MOST"
        if self.view.get("list") != title:
            self.view["list"] = title
            self.list_title.configure(text=title)
        if rows is None:
            rows = [(step, step_title(step), step_detail(step)) for step in list(upcoming)[:3]]
        rows = list(rows)[:3] + [(None, "", "")] * (3 - len(rows[:3]))
        self.view["rows"] = [row for row in rows if row[0] is not None]
        for index, (step, title_text, detail_text) in enumerate([(current, None, None)] + rows):
            if (step, title_text, detail_text) == self.icons[index]:
                continue
            self.icons[index] = (step, title_text, detail_text)
            canvas = self.icon if index == 0 else self.rows[index - 1][0]
            draw_icon(canvas, step, self.k, large=index == 0)
            if index:
                _, title, detail = self.rows[index - 1]
                title.configure(text=title_text)
                detail.configure(text=detail_text)

    def fit(self, name, text):
        """Show text in its fixed area, ending with … if it's longer than the area holds."""
        label = self.texts[name]
        limit = self.fonts[name].metrics("linespace") * LINES[name]
        label.configure(text=text)
        if label.winfo_reqheight() <= limit:
            return
        words = text.split()
        low, high = 0, len(words)
        while low < high:
            middle = (low + high + 1) // 2
            label.configure(text=" ".join(words[:middle]) + "…")
            if label.winfo_reqheight() <= limit:
                low = middle
            else:
                high = middle - 1
        label.configure(text=" ".join(words[:low]) + "…")

    def texts_now(self):
        """What the panel says, for tests."""
        return dict(self.view)


def draw_icon(canvas, step, k, large=False):
    """A small picture of the move: the side of the box, where the hands go, and from where."""
    canvas.delete("all")
    if step is None:
        return
    width, height = int(canvas.cget("width")), int(canvas.cget("height"))
    line = max(1, round((2.5 if large else 2) * k))
    dot = (5 if large else 3.6) * k
    face, phase = step
    if face == "align":
        for cx in (width * 0.3, width * 0.7):
            r = min(width, height) * 0.24
            canvas.create_arc(cx - r, height / 2 - r, cx + r, height / 2 + r, start=40, extent=280, style="arc",
                              outline=GOLD, width=line)
            canvas.create_line(cx + r * 0.77, height / 2 - r * 0.64, cx + r * 0.95, height / 2 - r * 0.15,
                               fill=GOLD, width=line, arrow="last", arrowshape=(5 * k, 6 * k, 2.5 * k))
        return
    if face == "corner":
        # One corner of a side, for how far it moved.
        face, key = phase
        scale = 0.46 + 0.16 * (1 - FACE_DEPTH[face])  # Smaller than the step pictures, to leave room for the ring.
        box_w, box_h = width * scale, height * scale
        x0, y0 = (width - box_w) / 2, (height - box_h) / 2
        canvas.create_rectangle(x0, y0, x0 + box_w, y0 + box_h, outline=FACE, width=line,
                                dash=() if face != "middle" else (4, 3))
        u, v = MARKS[key]
        x, y = x0 + u * box_w, y0 + v * box_h
        canvas.create_oval(x - 1.7 * dot, y - 1.7 * dot, x + 1.7 * dot, y + 1.7 * dot, outline=GOLD, width=line)
        canvas.create_oval(x - dot, y - dot, x + dot, y + dot, fill=GOLD, outline=DARK, width=1)
        return
    if face == "done":
        canvas.create_line(width * 0.3, height * 0.52, width * 0.45, height * 0.72, width * 0.72, height * 0.3,
                           fill=GOLD, width=line + 1, capstyle="round", joinstyle="round")
        return
    # Farther sides look smaller, like the box in the main window.
    scale = 0.55 + 0.25 * (1 - FACE_DEPTH[face])
    box_w, box_h = width * scale, height * scale
    x0, y0 = (width - box_w) / 2, (height - box_h) / 2

    def at(key, nudge=0.0):
        u, v = MARKS[key]
        return x0 + (u + nudge) * box_w, y0 + v * box_h

    canvas.create_rectangle(x0, y0, x0 + box_w, y0 + box_h, outline=FACE, width=line,
                            dash=() if face != "middle" else (4, 3))
    targets = PHASE_TARGETS[phase]
    nudges = (-0.07, 0.07) if targets[0] == targets[1] else (0.0, 0.0)
    ends = [at(key, nudge) for key, nudge in zip(targets, nudges)]
    if phase in PREVIOUS:
        starts = [at(key, nudge) for key, nudge in zip(PREVIOUS[phase], (-0.07, 0.07) if phase == "spread" else (0, 0))]
        for (sx, sy), (ex, ey) in zip(starts, ends):
            canvas.create_line(sx, sy, ex, ey, fill=TEXT, width=max(1, line - 1), dash=(3, 2), arrow="last",
                               arrowshape=(5 * k, 6 * k, 2.5 * k))
    for x, y in ends:
        canvas.create_oval(x - dot, y - dot, x + dot, y + dot, fill=GOLD, outline=DARK, width=1)
