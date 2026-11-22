"""A window for the webcam's own picture settings, with a live view of the camera you're adjusting.

Changes go straight to the camera, as they do from a camera maker's app, so they stay for every app.
MimedControl also saves them and applies them again whenever it opens that camera.
"""

import tkinter as tk
from time import monotonic
from tkinter import ttk

from .camera_controls import POWERLINE, POWERLINE_CHOICES
from . import theme

PAPER = theme.BG
INK = theme.INK
MUTED = theme.MUTED
ACCENT = theme.ACCENT
DARK = theme.DARK
SEND_EVERY = 0.08  # Seconds between changes sent while a slider moves.
PREVIEW = (320, 240)


class CameraSettingsWindow:
    def __init__(self, root, sessions, remember, on_close, idle_message=None):
        """sessions() lists (title, session) for running cameras. remember(name, key, choice) saves a
        choice such as {"value": 120} or {"auto": True}, and None forgets it. idle_message() says
        what's happening while no camera runs, such as a camera error."""
        self.root, self.sessions, self.remember, self.on_close = root, sessions, remember, on_close
        self.idle_message = idle_message
        self.window = window = tk.Toplevel(root)
        window.title("Camera settings")
        window.configure(bg=PAPER)
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(window, padding=(20, 16, 20, 16))
        frame.grid()
        tk.Label(frame, text="Camera settings", bg=PAPER, fg=INK, font=theme.font(16, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        tk.Label(frame, text="Changes go straight to the camera and stay for every app. MimedControl "
                             "applies them again each time it opens this camera.", bg=PAPER, fg=MUTED,
                 wraplength=760, justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        top = ttk.Frame(frame)
        top.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        tk.Label(top, text="Camera", bg=PAPER, fg=MUTED).pack(side="left", padx=(0, 10))
        self.choice = tk.StringVar()
        self.picker = ttk.Combobox(top, textvariable=self.choice, state="readonly", width=34)
        self.picker.pack(side="left")
        self.picker.bind("<<ComboboxSelected>>", lambda _event: self.select())
        # The live view keeps one size whatever the camera sends, so the sliders never move.
        slot = tk.Frame(frame, width=PREVIEW[0], height=PREVIEW[1], bg=DARK)
        slot.grid_propagate(False)
        slot.pack_propagate(False)
        slot.grid(row=3, column=0, sticky="nw", padx=(0, 18))
        self.preview = tk.Label(slot, bg=DARK, fg="white", text="Starting camera…")
        self.preview.pack(fill="both", expand=True)
        self.image = self.photo = None
        self.rows_frame = ttk.Frame(frame)
        self.rows_frame.grid(row=3, column=1, sticky="nw")
        self.message = tk.StringVar()
        tk.Label(frame, textvariable=self.message, bg=PAPER, fg=ACCENT, wraplength=760, justify="left",
                 anchor="w", height=2).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="Done", style="Primary.TButton", command=self.close).pack(side="left")
        self.reset_button = ttk.Button(buttons, text="Reset to camera defaults", command=self.reset)
        self.reset_button.pack(side="left", padx=(8, 0))
        self.rows = {}
        self.layout = None
        self.session = None
        self.sent = {}
        self.scheduled = {}
        self.jobs = set()
        self.closed = False
        self.refresh()

    def later(self, ms, function):
        """A timer on the main window, cancelled when this window closes."""
        def run():
            self.jobs.discard(job)
            if not self.closed:
                function()
        job = self.root.after(ms, run)
        self.jobs.add(job)
        return job

    # Cameras
    def choices(self):
        return [(title, session) for title, session in self.sessions() if session is not None]

    def select(self):
        session = dict(self.choices()).get(self.choice.get())
        if session is self.session:
            return
        self.watch(session)
        self.clear_rows()
        self.image = None
        self.preview.configure(image="", text="Starting camera…")
        self.photo = None

    def watch(self, session):
        """Follow one camera: its automatic values and its live view."""
        previous = self.session
        if previous is not None and previous is not session:
            previous.watch_controls.clear()
            if previous.auxiliary:
                previous.set_preview(False)
        self.session = session
        if session is not None:
            session.watch_controls.set()
            session.set_preview(True)

    # Keeping the window current
    def refresh(self):
        if self.closed:
            return
        choices = self.choices()
        titles = [title for title, _ in choices]
        if list(self.picker.cget("values")) != titles:
            self.picker.configure(values=titles)
        if self.choice.get() not in titles:
            self.choice.set(titles[0] if titles else "")
        if dict(choices).get(self.choice.get()) is not self.session:
            self.select()
        session = self.session
        if session is not None and session.preview_image is not None and session.preview_image is not self.image:
            self.image = session.preview_image
            # Show the new picture before letting go of the old one, which Tk deletes with it.
            photo = tk.PhotoImage(data=session.preview_image, format="ppm")
            self.preview.configure(image=photo, text="")
            self.photo = photo
        controls = session.camera_controls if session is not None else None
        if controls is None:
            if self.layout is not None:
                self.clear_rows()
            if session is not None:
                note = session.controls_note or ("Reading the camera's settings…" if session.backend
                                                 else session.connection_status or "Starting camera…")
            else:
                note = (self.idle_message() if self.idle_message else "") or "Start the camera to change its settings."
            self.message.set(note)
            self.reset_button.state(("disabled",))
        else:
            layout = tuple((c.key, c.can_auto, c.minimum, c.maximum) for c in controls)
            if layout != self.layout:
                self.build_rows(controls)
                self.layout = layout
                self.message.set("")
            self.show_values(controls)
            self.reset_button.state(("!disabled",))
        self.later(120, self.refresh)

    def clear_rows(self):
        for widget in self.rows_frame.winfo_children():
            widget.destroy()
        self.rows = {}
        self.layout = None

    def build_rows(self, controls):
        self.clear_rows()
        for row, control in enumerate(controls):
            tk.Label(self.rows_frame, text=control.label, bg=PAPER, fg=INK, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            auto = tk.BooleanVar(value=control.auto)
            if control.can_auto and control.can_manual:
                ttk.Checkbutton(self.rows_frame, text="Auto", variable=auto,
                                command=lambda key=control.key: self.set_auto(key)).grid(row=row, column=1, sticky="w")
            value = tk.DoubleVar(value=control.value)
            text = tk.StringVar(value=control.describe())
            widgets = []
            if control.key == POWERLINE:
                choices = ttk.Frame(self.rows_frame)
                choices.grid(row=row, column=2, columnspan=2, sticky="w")
                for number, label in POWERLINE_CHOICES:
                    if control.minimum <= number <= control.maximum:
                        ttk.Radiobutton(choices, text=label, value=number, variable=value,
                                        command=lambda key=control.key: self.slide(key, final=True)).pack(
                            side="left", padx=(0, 10))
            else:
                scale = ttk.Scale(self.rows_frame, from_=control.minimum, to=control.maximum, variable=value,
                                  length=240, command=lambda _value, key=control.key: self.slide(key))
                scale.grid(row=row, column=2, sticky="w", padx=(6, 8))
                scale.bind("<ButtonRelease-1>", lambda _event, key=control.key: self.slide(key, final=True))
                widgets.append(scale)
                tk.Label(self.rows_frame, textvariable=text, bg=PAPER, fg=INK, width=11, anchor="w").grid(
                    row=row, column=3, sticky="w")
            self.rows[control.key] = {"control": control, "auto": auto, "value": value, "text": text,
                                      "widgets": widgets, "moving": False}

    def show_values(self, controls):
        for control in controls:
            row = self.rows.get(control.key)
            if row is None:
                continue
            row["control"] = control
            moving = row["moving"] or control.key in self.scheduled
            if not moving:
                if row["value"].get() != control.value:
                    row["value"].set(control.value)
                row["text"].set(control.describe())
            if row["auto"].get() != control.auto and control.key not in self.sent:
                row["auto"].set(control.auto)
            for widget in row["widgets"]:
                widget.state(("disabled",) if row["auto"].get() else ("!disabled",))

    # Changes
    def slide(self, key, final=False):
        row = self.rows.get(key)
        if row is None or self.session is None:
            return
        control = row["control"]
        if row["auto"].get():
            return
        value = control.clamp(row["value"].get())
        row["text"].set(control.describe(value))
        row["moving"] = not final
        now = monotonic()
        last = self.sent.get(key, (0.0, None))[0]
        if final or now - last >= SEND_EVERY:
            self.send(key, value, final)
        elif key not in self.scheduled:
            self.scheduled[key] = self.later(max(1, int(1000 * (SEND_EVERY - (now - last)))),
                                             lambda: self.send_latest(key))

    def send_latest(self, key):
        self.scheduled.pop(key, None)
        row = self.rows.get(key)
        if row is not None and not row["auto"].get():
            self.send(key, row["control"].clamp(row["value"].get()), final=not row["moving"])

    def send(self, key, value, final):
        if key in self.scheduled:
            job = self.scheduled.pop(key)
            self.jobs.discard(job)
            self.root.after_cancel(job)
        self.sent[key] = (monotonic(), value)
        self.session.change_control(key, value=value, auto=False)
        if final:
            self.remember(self.session.camera_name, key, {"value": value})
            self.later(400, lambda: self.sent.pop(key, None))

    def set_auto(self, key):
        row = self.rows.get(key)
        if row is None or self.session is None:
            return
        auto = row["auto"].get()
        self.sent[key] = (monotonic(), None)
        self.later(600, lambda: self.sent.pop(key, None))
        if auto:
            self.session.change_control(key, auto=True)
            self.remember(self.session.camera_name, key, {"auto": True})
        else:
            value = row["control"].clamp(row["value"].get())
            self.session.change_control(key, value=value, auto=False)
            self.remember(self.session.camera_name, key, {"value": value})
        for widget in row["widgets"]:
            widget.state(("disabled",) if auto else ("!disabled",))

    def reset(self):
        if self.session is None or self.session.camera_controls is None:
            return
        for control in self.session.camera_controls:
            if control.can_auto:
                self.session.change_control(control.key, auto=True)
            else:
                self.session.change_control(control.key, value=control.default, auto=False)
            self.remember(self.session.camera_name, control.key, None)
        self.message.set("Back to the camera's own defaults.")

    def close(self):
        if self.closed:
            return
        self.closed = True
        for job in self.jobs:
            self.root.after_cancel(job)
        self.jobs.clear()
        self.scheduled.clear()
        self.watch(None)
        self.preview.configure(image="")
        self.photo = None
        self.window.destroy()
        self.on_close()
