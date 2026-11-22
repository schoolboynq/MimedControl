import json
import sys
import tkinter as tk
import traceback
from datetime import datetime
from threading import Thread
from time import monotonic
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from dataclasses import replace

from .aim import RESPONSES
from .arm_gestures import FistGestures
from .automatic_alignment import hand_features, open_hand, solve_video_tracks
from .calibration import (CORNER_NAMES, DONE_STEP, FACE_TITLES, FACES, BoxCalibration, calibration_plan, load_reach,
                          palm_scale, profile_path, save_reach)
from .camera import PREVIEW_SIZE, CameraSession
from .camera_controls import clean_saved
from .camera_filters import CAMERA_FILTERS
from .desktop import PAD_SHORTCUT, PAUSE_SHORTCUT, configure_dpi, keep_pad_inactive, open_desktop
from .gestures import CLICK_GESTURES, DEFAULT_PALM_TRAVEL, PAD_KEYS, PALM_AREAS, PALM_MODES, GestureState, aim_landmark, pad_key
from .relaxed_hand import RELAXED_HAND
from .reach_cube import ReachCube
from .gesture_controls import (ACTIONS, DIRECTIONS, GESTURE_NAMES, GESTURES, available_actions, clean_choices,
                               describe, format_shortcut, keys_for, parse_shortcut)
from .screens import EdgeSwitch
from .sticky import StickyPointer
from .steps_panel import StepsPanel
from . import theme
from .stereo import average_profiles, load_profile, save_profile, settings_path

INK = theme.INK
PAPER = theme.BG
CARD = theme.CARD
MUTED = theme.MUTED
ACCENT = theme.ACCENT
DARK = theme.DARK
KEY_BG = "#262a33"
KEY_ACTIVE = theme.ACCENT
NO_SECOND = "None"
WIDTHS = (("Smaller", "75%"), ("As drawn", "100%"), ("Larger", "125%"))
CUBE_SCALE = 1.55
# The calibration view keeps one size for the box and for both camera views, so nothing moves.
STAGE = (2 * PREVIEW_SIZE[0] + 30, round(235 * CUBE_SCALE))
CALIBRATING = "Desktop gestures are paused during calibration."
WAITING = ("Sit where you normally use the computer, with both hands free. Calibration starts as soon as "
           "the camera is ready.")
ERROR_LOG_LIMIT = 200_000
WIDTH = 480
PAGES = (("setup", "Set up"), ("calibrate", "Calibrate"), ("use", "Use"))
MODE_HINTS = {
    RELAXED_HAND: ("Aim with your knuckles and bring index and middle to your thumb to click, or hold them there "
                   "and move to drag. The most relaxed mode."),
    "Palm + pinch": "Aim with your palm. Pinch thumb and index to click, or hold the pinch and move to drag.",
    "Pinch focus": "Aim from a loose C between thumb and index. Touch them to click, or hold and move to drag.",
    "Point + pinch": "Point with your index finger. Pinch to click, or hold the pinch and move to drag.",
    "Air tap": "Point with your index finger. Tap toward the camera to click.",
}
SWIPE_DIRECTIONS = {(-1, 0): "left", (1, 0): "right", (0, -1): "up", (0, 1): "down"}
DRAG_GUIDE = {
    RELAXED_HAND: "Keep them together and move",
    "Palm + pinch": "Keep the pinch and move",
    "Pinch focus": "Keep fingertips together and move",
    "Point + pinch": "Keep the pinch and move",
    "Air tap": "Choose a pinch mode to drag",
}
CLICK_GUIDE = {
    RELAXED_HAND: ("Extend thumb, index and middle. Relax ring and pinky",
                   "Bring index and middle together to thumb"),
    "Palm + pinch": ("Aim with the center of your knuckles", "Pinch thumb and index, then reopen"),
    "Pinch focus": ("Thumb and index slightly apart", "Touch fingertips, then reopen"),
    "Point + pinch": ("Point with index finger", "Touch fingertips, then reopen"),
    "Air tap": ("Point with index finger", "Tap forward, then pull back"),
}


class NumberPad:
    def __init__(self, root):
        self.window = tk.Toplevel(root, bg=DARK, takefocus=False)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.title("Webcam number pad")
        self.keys = {}
        self.selected = None
        panel = tk.Frame(self.window, bg=DARK, padx=12, pady=10)
        panel.pack()
        tk.Label(panel, text="Number pad", bg=DARK, fg="white", font=theme.font(12, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
        )
        names = {"Backspace": "⌫", "Enter": "↵", "-": "−"}
        for row, keys in enumerate(PAD_KEYS, 1):
            for column, key in enumerate(keys):
                if key in self.keys:
                    continue
                label = tk.Label(panel, text=names.get(key, key), bg=KEY_BG, fg="white",
                                 font=theme.font(16, "bold"), width=3, height=2)
                label.grid(row=row, column=column, columnspan=2 if key == "0" else 1,
                           padx=2, pady=2, sticky="nsew")
                self.keys[key] = label
        tk.Label(panel, text=f"{PAD_SHORTCUT} to close", bg=DARK,
                 fg=MUTED, font=theme.font(9)).grid(row=5, column=0, columnspan=4, pady=(6, 0))
        try:
            keep_pad_inactive(self.window)
        except tk.TclError:
            pass  # Tk versions without MacWindowStyle still use an override-redirect window.

    def show(self, monitor):
        self.highlight(None)
        if monitor is not None:
            left, top, right, bottom = monitor
            self.window.update_idletasks()
            x = max(left, right - self.window.winfo_reqwidth() - 22)
            y = max(top, min(top + 46, bottom - self.window.winfo_reqheight()))
            # A leading '+' uses absolute placement even at a negative desktop coordinate.
            self.window.geometry(f"+{x}+{y}")
            self.window.deiconify()
            self.window.lift()
        else:
            self.window.withdraw()

    def highlight(self, key):
        if key == self.selected:
            return
        if self.selected is not None:
            self.keys[self.selected].configure(bg=KEY_BG, fg="white")
        if key is not None:
            self.keys[key].configure(bg=KEY_ACTIVE, fg=DARK)
        self.selected = key


def same_hand_first(sample):
    """Put the same physical hand first in every frame: the one on the right of the mirrored view.

    The tracker can swap which hand it lists first. Both previews are mirrored and
    face you, so left-to-right order identifies each hand in both cameras.
    """
    sample = replace(sample, preview=None)
    if sample.second is not None and sample.second[[0, 5, 9, 13, 17], 0].mean() > sample.points[[0, 5, 9, 13, 17], 0].mean():
        sample = replace(sample, points=sample.second, second=sample.points,
                         world=sample.second_world, second_world=sample.world)
    return sample


class App:
    def __init__(self, root):
        self.root = root
        self.session = None
        self.desktop = None
        self.gestures = GestureState()
        self.reach_key = self.saved_reach = self.capture = None
        self.setup_open = False
        self.calibration_step = self.alignment = None
        self.calibration_origin = "use"
        self.calibration_mode = ("fresh", None)  # Or ("refine", None), or ("redo", side).
        self.summary_job = None
        self.refinable = False
        self.previous_profile = None
        self.calibration_view = None
        self.pair_problem = ""
        self.pair_images = [None, None]
        self.calibrate_on_start = False
        self.testing = False
        self.page = None
        self.reach_enabled = tk.BooleanVar(value=True)
        self.pad_enabled = False
        self.pad = None
        self.last_frame = None
        self.last_pointer = None
        self.next_pointer_check = 0.0
        self.next_display_check = 0.0
        self.display_info = tk.StringVar()
        self.camera_mode = tk.StringVar()
        self.second_hint = tk.StringVar()
        self.show_clicks = tk.BooleanVar(value=sys.platform == "win32")
        self.click_indicator = None
        self.sticky_enabled = tk.BooleanVar(value=sys.platform == "win32")
        self.sticky = StickyPointer(root.winfo_fpixels("1i"))
        self.sticky_targets = None
        self.drag_enabled = tk.BooleanVar(value=True)
        self.holding = self.dragged = False
        self.press_pixel = None
        self.feedback_until = 0.0
        self.feedback = ""
        self.preview_enabled = tk.BooleanVar()
        self.two_hands = tk.BooleanVar()
        self.response = tk.StringVar(value=self.gestures.response)
        self.palm_travel = tk.StringVar(value=self.gestures.palm_travel)
        self.click_gesture = tk.StringVar(value=self.gestures.click_gesture)
        self.instructions = [tk.StringVar() for _ in range(5)]
        self.drag_instruction = tk.StringVar()
        self.camera = tk.StringVar(value="0")
        self.second_camera = tk.StringVar(value=NO_SECOND)
        self.camera_filter = tk.StringVar(value=CAMERA_FILTERS[0])
        self.control_width = tk.StringVar(value="100%")
        self.camera_names = None
        self.fists = FistGestures()
        self.edge = EdgeSwitch()
        self.edge_message = ""
        self.fist_enabled = tk.BooleanVar(value=True)
        self.gesture_actions, self.gesture_shortcuts = clean_choices({}, {})
        self.palm_action = tk.StringVar()
        self.fist_guide = tk.StringVar()
        self.screen_guide = tk.StringVar()
        self.customize_window = None
        self.camera_window = None
        self.settings_only = False  # Opened just for camera settings, with --camera-settings.
        self.control_cameras = []  # Cameras that always need control, so they open with it first.
        self.camera_settings = {}  # Picture settings chosen here, per camera name.
        self.save_job = None
        self.steps = None
        self.steps_position = None
        self.frames_lost = False
        self.error_details = ""
        self.camera_label = tk.StringVar()
        self.second_label = tk.StringVar()
        self.mode_hint = tk.StringVar()
        self.calibration_info = tk.StringVar()
        self.status = tk.StringVar()
        self.stereo_info = tk.StringVar()
        self.image = None
        self.closing = False
        self.load_settings()
        self.update_guide()
        self.update_gesture_guide()

        root.title("MimeControl")
        root.configure(bg=PAPER)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self.report_callback_error
        root.bind("<Escape>", lambda _: self.pause())
        root.bind("<Map>", self.refresh_preview)
        root.bind("<Unmap>", self.refresh_preview)

        theme.apply(root)
        theme.set_icon(root)
        root.after(0, lambda: theme.dark_title_bar(root))

        content = ttk.Frame(root, padding=(22, 18, 22, 17))
        content.grid()
        content.columnconfigure(0, minsize=WIDTH)
        heading = ttk.Frame(content)
        heading.grid(row=0, column=0, sticky="ew")
        self.logo = theme.scaled_image(root, "logo", (40, 50, 60, 80))
        if self.logo is not None:
            tk.Label(heading, image=self.logo, bg=PAPER, bd=0).pack(side="left", padx=(0, 10))
        # The wordmark: "Mime" in white and "Control" in amber, set as one word.
        self.title_font = tkfont.Font(root=root, family=theme.FAMILY, size=22, weight="bold")
        first, whole = self.title_font.measure("Mime"), self.title_font.measure("MimeControl")
        line = self.title_font.metrics("linespace")
        mark = tk.Canvas(heading, width=whole + 2, height=line, bg=PAPER, highlightthickness=0, bd=0)
        mark.create_text(0, line / 2, text="Mime", anchor="w", font=self.title_font, fill=INK)
        mark.create_text(first, line / 2, text="Control", anchor="w", font=self.title_font, fill=ACCENT)
        mark.pack(side="left")
        chips = tk.Frame(heading, bg=PAPER)
        chips.pack(side="right")
        self.camera_chip = tk.Label(chips, textvariable=self.camera_mode, bg=theme.FIELD, fg=MUTED, padx=10, pady=3,
                                    font=theme.font(9, "bold"))
        self.camera_chip.pack(anchor="e")
        tk.Label(chips, textvariable=self.display_info, bg=PAPER, fg=MUTED, font=theme.font(9)).pack(anchor="e",
                                                                                                pady=(4, 0))

        # Where you are in the flow: set up, calibrate, then use.
        steps = ttk.Frame(content)
        steps.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.step_labels = {}
        for index, (key, name) in enumerate(PAGES):
            if index:
                tk.Label(steps, text="›", bg=PAPER, fg=MUTED).pack(side="left", padx=6)
            label = tk.Label(steps, text=f"{index + 1}  {name}", bg=PAPER, fg=MUTED)
            label.pack(side="left")
            self.step_labels[key] = label

        status_card = theme.Card(content, padding=(14, 11), width=WIDTH)
        status_card.grid(row=2, column=0, sticky="ew", pady=(12, 12))
        status_area = status_card.body
        status_area.columnconfigure(1, weight=1)
        self.status_light = tk.Canvas(status_area, width=12, height=12, bg=CARD, highlightthickness=0)
        self.status_light.create_oval(1, 1, 11, 11, fill=theme.DIM, outline="", tags="light")
        self.status_light.grid(row=0, column=0, sticky="n", padx=(0, 10), pady=(5, 0))
        tk.Label(status_area, textvariable=self.status, bg=CARD, fg=INK, wraplength=WIDTH - 60,
                 justify="left", anchor="w", font=theme.font(11)).grid(row=0, column=1, sticky="w")
        self.copy_error_button = ttk.Button(status_area, text="Copy camera error", command=self.copy_camera_error)
        self.copy_error_button.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.copy_error_button.grid_remove()
        self.stereo_label = tk.Label(status_area, textvariable=self.stereo_info, bg=CARD, fg=MUTED,
                                     wraplength=WIDTH - 60, justify="left")
        self.stereo_label.grid(row=2, column=1, sticky="w", pady=(4, 0))
        self.stereo_label.grid_remove()

        # 1 · Set up
        self.setup_page = ttk.Frame(content)
        form_card = theme.Card(self.setup_page, width=WIDTH)
        form_card.pack(fill="x")
        form = form_card.body
        form.columnconfigure(1, weight=1)
        for row, text in enumerate(("Main camera", "Second camera", "Control mode", "Control width")):
            tk.Label(form, text=text, bg=CARD, fg=MUTED).grid(row=row * 2, column=0, sticky="w", padx=(0, 10), pady=3)
        self.camera_picker = ttk.Combobox(form, textvariable=self.camera_label, state="readonly", width=30)
        self.camera_picker.grid(row=0, column=1, sticky="ew", pady=3)
        self.camera_picker.bind("<<ComboboxSelected>>", self.choose_camera)
        self.second_picker = ttk.Combobox(form, textvariable=self.second_label, state="readonly", width=30)
        self.second_picker.grid(row=2, column=1, sticky="ew", pady=3)
        self.second_picker.bind("<<ComboboxSelected>>", self.choose_second_camera)
        self.second_hint_label = tk.Label(form, textvariable=self.second_hint, bg=CARD, fg=ACCENT,
                                          font=theme.font(9), wraplength=WIDTH - 150, justify="left")
        self.second_hint_label.grid(row=3, column=1, sticky="w", pady=(0, 4))
        click_picker = ttk.Combobox(form, textvariable=self.click_gesture, values=CLICK_GESTURES,
                                    state="readonly", width=30)
        click_picker.grid(row=4, column=1, sticky="ew", pady=3)
        click_picker.bind("<<ComboboxSelected>>", self.set_click_gesture)
        tk.Label(form, textvariable=self.mode_hint, bg=CARD, fg=MUTED, font=theme.font(9),
                 wraplength=WIDTH - 150, justify="left").grid(row=5, column=1, sticky="w")
        self.width_choices(form, card=True).grid(row=6, column=1, sticky="w", pady=3)
        tk.Label(form, text="How much of the box you draw spans the screen. Smaller needs less arm movement.",
                 bg=CARD, fg=MUTED, font=theme.font(9), wraplength=WIDTH - 150,
                 justify="left").grid(row=7, column=1, sticky="w")
        tk.Label(form, text="Gestures", bg=CARD, fg=MUTED).grid(row=8, column=0, sticky="w", padx=(0, 10), pady=3)
        links = ttk.Frame(form, style="Card.TFrame")
        links.grid(row=8, column=1, sticky="w", pady=3)
        ttk.Button(links, text="Customize gestures…", style="CardLink.TButton",
                   command=self.customize_gestures).pack(side="left")
        if sys.platform == "win32":
            ttk.Button(links, text="Camera settings…", style="CardLink.TButton",
                       command=self.open_camera_settings).pack(side="left", padx=(16, 0))

        tk.Label(self.setup_page, textvariable=self.calibration_info, bg=PAPER, fg=INK, wraplength=WIDTH,
                 justify="left", anchor="w").pack(fill="x", pady=(14, 10))
        actions = ttk.Frame(self.setup_page)
        actions.pack(fill="x")
        self.start_button = ttk.Button(actions, style="Primary.TButton")
        self.start_button.pack(side="left")
        self.alternate_button = ttk.Button(actions)
        self.alternate_button.pack(side="left", padx=(8, 0))
        self.test_button = ttk.Button(actions, text="Check camera", command=self.test_camera)
        self.test_button.pack(side="right")
        self.refine_row = ttk.Frame(self.setup_page)
        self.refine_row.pack(fill="x")
        self.refine_link = ttk.Button(self.refine_row, text="Refine or redo a side…", style="Link.TButton",
                                      command=self.calibrate_menu_from_setup)

        self.more_button = ttk.Button(self.setup_page, text="More settings ▸", style="Link.TButton",
                                      command=self.toggle_more_settings)
        self.more_button.pack(anchor="w", pady=(12, 0))
        self.more_settings = theme.Card(self.setup_page, width=WIDTH)
        more = self.more_settings.body
        more.columnconfigure(1, weight=1)
        pickers = (
            ("Pointer response", self.response, tuple(RESPONSES), self.set_response),
            ("Camera filter", self.camera_filter, CAMERA_FILTERS, self.set_camera_filter),
        )
        for row, (text, variable, values, command) in enumerate(pickers):
            tk.Label(more, text=text, bg=CARD, fg=MUTED).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            picker = ttk.Combobox(more, textvariable=variable, values=values, state="readonly", width=14)
            picker.grid(row=row, column=1, sticky="w", pady=3)
            picker.bind("<<ComboboxSelected>>", command)
        self.travel_options = ttk.Frame(more, style="Card.TFrame")
        self.travel_options.grid(row=2, column=0, columnspan=2, sticky="w", pady=3)
        tk.Label(self.travel_options, text="Palm aim area", bg=CARD, fg=MUTED).pack(side="left", padx=(0, 10))
        travel_picker = ttk.Combobox(self.travel_options, textvariable=self.palm_travel, values=tuple(PALM_AREAS),
                                     state="readonly", width=10)
        travel_picker.pack(side="left")
        travel_picker.bind("<<ComboboxSelected>>", self.set_palm_travel)
        tk.Label(self.travel_options, text="Smaller brings the screen edges closer", bg=CARD, fg=MUTED,
                 font=theme.font(9)).pack(side="left", padx=(8, 0))
        toggles = ttk.Frame(more, style="Card.TFrame")
        toggles.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        options = [
            ("Use calibration", self.reach_enabled, self.apply_reach),
            ("Two-hand controls", self.two_hands, self.set_two_hands),
            ("Hold to drag", self.drag_enabled, self.set_drag),
        ]
        if sys.platform == "win32":
            options += [("Sticky targets", self.sticky_enabled, self.set_sticky),
                        ("Show clicks", self.show_clicks, self.update_click_indicator)]
        for index, (text, variable, command) in enumerate(options):
            ttk.Checkbutton(toggles, text=text, variable=variable, command=command, style="Card.TCheckbutton").grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=2)

        # 2 · Calibrate. The words are in the steps panel; this page only shows pictures of fixed size.
        self.setup_panel = ttk.Frame(content)
        header = ttk.Frame(self.setup_panel)
        header.pack(fill="x")
        self.calibration_heading = tk.StringVar(value="Draw your control box")
        tk.Label(header, textvariable=self.calibration_heading, bg=PAPER, fg=INK, font=theme.font(20, "bold")).pack(side="left")
        ttk.Button(header, text="Show steps", style="Link.TButton", command=self.show_steps).pack(side="right")
        self.stage = tk.Frame(self.setup_panel, width=STAGE[0], height=STAGE[1], bg=DARK)
        self.stage.pack_propagate(False)
        self.stage.pack(anchor="w", pady=(8, 8))
        self.cube = ReachCube(self.stage, scale=CUBE_SCALE)
        self.pair_previews = tk.Frame(self.stage, bg=DARK)
        self.pair_labels, self.pair_captions = [], []
        for column, name in enumerate(("Main camera", "Second camera")):
            slot = tk.Frame(self.pair_previews, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1], bg="#0a0b0e")
            slot.pack_propagate(False)
            slot.grid(row=0, column=column, padx=5)
            label = tk.Label(slot, bg="#0a0b0e", fg=MUTED, text="Starting…")
            label.pack(fill="both", expand=True)
            caption = tk.Label(self.pair_previews, text=name, bg=DARK, fg=INK, width=34, anchor="w")
            caption.grid(row=1, column=column, padx=5, pady=(4, 0), sticky="w")
            self.pair_labels.append(label)
            self.pair_captions.append(caption)
        buttons = ttk.Frame(self.setup_panel)
        self.setup_buttons = buttons
        buttons.pack(anchor="w")
        self.restart_button = ttk.Button(buttons, text="Start over", command=self.begin_calibration)
        self.restart_button.pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self.cancel_calibration).pack(side="left", padx=(8, 0))

        # 3 · Use
        self.use_page = ttk.Frame(content)
        controls = ttk.Frame(self.use_page)
        controls.pack(fill="x")
        ttk.Button(controls, text="Pause", style="Primary.TButton", command=self.pause).pack(side="left")
        self.pad_button = ttk.Button(controls, text="Number pad", command=lambda: self.set_pad(not self.pad_enabled))
        self.pad_button.pack(side="left", padx=(8, 0))
        self.calibrate_button = ttk.Menubutton(controls, text="Calibrate")
        self.calibrate_menu = tk.Menu(self.calibrate_button, tearoff=False)
        self.calibrate_button.configure(menu=self.calibrate_menu)
        self.calibrate_button.pack(side="left", padx=(8, 0))
        self.calibrate_menu.add_command(label="Refine: draw the box again",
                                        command=lambda: self.request_calibration("refine"))
        sides = tk.Menu(self.calibrate_menu, tearoff=False)
        for face in FACES:
            sides.add_command(label=FACE_TITLES[face], command=lambda face=face: self.request_calibration("redo", face))
        self.calibrate_menu.add_cascade(label="Redo one side", menu=sides)
        self.calibrate_menu.add_separator()
        self.calibrate_menu.add_command(label="Calibrate from scratch", command=self.request_calibration)
        self.update_calibrate_menu(False)
        ttk.Button(controls, text="Gestures…", command=self.customize_gestures).pack(side="left", padx=(8, 0))
        if sys.platform == "win32":
            ttk.Button(controls, text="Camera…", command=self.open_camera_settings).pack(side="left", padx=(8, 0))
        use_options = ttk.Frame(self.use_page)
        use_options.pack(fill="x", pady=(10, 6))
        ttk.Checkbutton(use_options, text="Show camera preview", variable=self.preview_enabled,
                        command=self.refresh_preview).pack(side="left")
        self.width_choices(use_options).pack(side="right")
        tk.Label(use_options, text="Width", bg=PAPER, fg=MUTED).pack(side="right", padx=(0, 6))
        guide_card = theme.Card(self.use_page, width=WIDTH)
        guide_card.pack(fill="x")
        guide = guide_card.body
        for row, (gesture, action) in enumerate((
            (self.instructions[0], "Move the pointer"),
            (self.instructions[1], "Click or press a number"),
            (self.drag_instruction, "Hold and drag"),
            (self.instructions[2], "Right-click"),
            (self.instructions[3], "Scroll"),
            (self.instructions[4], "Zoom in or out"),
            ("Spread fingers and hold still", self.palm_action),
        )):
            label = tk.Label(guide, bg=CARD, fg=INK, anchor="w", wraplength=WIDTH - 200, justify="left")
            if isinstance(gesture, tk.StringVar):
                label.configure(textvariable=gesture)
            else:
                label.configure(text=gesture)
            label.grid(row=row, column=0, sticky="w", pady=2)
            target = tk.Label(guide, bg=CARD, fg=ACCENT, anchor="w")
            if isinstance(action, tk.StringVar):
                target.configure(textvariable=action)
            else:
                target.configure(text=action)
            target.grid(row=row, column=1, sticky="w", padx=(13, 0))
        tk.Label(guide, textvariable=self.fist_guide, bg=CARD, fg=MUTED, anchor="w", justify="left",
                 wraplength=WIDTH - 40).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.swipe_guide = tk.Label(guide, textvariable=self.screen_guide, bg=CARD, fg=MUTED, anchor="w",
                                    justify="left", wraplength=WIDTH - 40)
        self.swipe_guide.grid(row=8, column=0, columnspan=2, sticky="w", pady=2)
        self.swipe_guide.grid_remove()
        self.two_hand_guide = tk.Label(self.use_page, bg=PAPER, fg=MUTED, justify="left", anchor="w", wraplength=WIDTH,
                                       text="Show your pointer hand first. While aiming, move your other open palm "
                                            "up or down to scroll. Move both open palms apart or together to zoom.")

        self.pages = {"setup": self.setup_page, "calibrate": self.setup_panel, "use": self.use_page}
        for page in self.pages.values():
            page.grid(row=3, column=0, sticky="ew")
            page.grid_remove()
        self.preview_label = tk.Label(content, text="Starting camera…", bg=DARK, fg="white", padx=14, pady=14)
        self.preview_label.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.preview_label.grid_remove()
        tk.Label(content, text=f"{PAUSE_SHORTCUT} to pause  ·  {PAD_SHORTCUT} for number pad", bg=PAPER, fg=MUTED,
                 font=theme.font(9)).grid(row=5, column=0, sticky="w", pady=(14, 0))

        self.list_cameras()
        self.find_camera_names()
        self.show_page("setup")
        self.set_status("Choose your camera and control mode, then start.")

    # Settings that carry over between launches.
    @staticmethod
    def settings_file():
        return profile_path(0, 0).with_name("settings.json")

    def load_settings(self):
        try:
            data = json.loads(self.settings_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        choices = (
            ("camera", self.camera, tuple(str(i) for i in range(10))),
            ("click_gesture", self.click_gesture, CLICK_GESTURES),
            ("response", self.response, tuple(RESPONSES)),
            ("camera_filter", self.camera_filter, CAMERA_FILTERS),
            ("palm_travel", self.palm_travel, tuple(PALM_AREAS)),
            ("control_width", self.control_width, tuple(value for _, value in WIDTHS)),
        )
        for key, variable, allowed in choices:
            if data.get(key) in allowed:
                variable.set(data[key])
        self.gesture_actions, self.gesture_shortcuts = clean_choices(data.get("gesture_actions"),
                                                                     data.get("gesture_shortcuts"))
        self.camera_settings = clean_saved(data.get("camera_settings"))
        names = data.get("control_cameras")
        self.control_cameras = [name for name in names if isinstance(name, str)][:20] if isinstance(names, list) else []
        position = data.get("steps_panel")
        if isinstance(position, list) and len(position) == 2 and all(isinstance(v, int) for v in position):
            self.steps_position = tuple(position)
        for key, variable in (("two_hands", self.two_hands), ("sticky", self.sticky_enabled),
                              ("show_clicks", self.show_clicks), ("preview", self.preview_enabled),
                              ("use_calibration", self.reach_enabled), ("fist_gestures", self.fist_enabled),
                              ("drag", self.drag_enabled)):
            if isinstance(data.get(key), bool):
                variable.set(data[key])
        second = data.get("second_camera")
        if isinstance(second, str) and (second == NO_SECOND or second in tuple(str(i) for i in range(10))):
            self.second_camera.set(second)
        else:
            self.second_camera.set(self.saved_second_camera(int(self.camera.get())))
        self.gestures.click_gesture = self.click_gesture.get()
        self.gestures.response = self.response.get()
        self.gestures.palm_travel = self.palm_travel.get()
        self.gestures.drag_enabled = self.drag_enabled.get()

    def save_settings(self):
        data = {
            "camera": self.camera.get(), "second_camera": self.second_camera.get(),
            "click_gesture": self.click_gesture.get(), "response": self.response.get(),
            "camera_filter": self.camera_filter.get(), "palm_travel": self.palm_travel.get(),
            "two_hands": self.two_hands.get(), "sticky": self.sticky_enabled.get(),
            "show_clicks": self.show_clicks.get(), "preview": self.preview_enabled.get(),
            "use_calibration": self.reach_enabled.get(), "control_width": self.control_width.get(),
            "fist_gestures": self.fist_enabled.get(), "gesture_actions": self.gesture_actions,
            "gesture_shortcuts": self.gesture_shortcuts, "camera_settings": self.camera_settings,
            "control_cameras": self.control_cameras, "drag": self.drag_enabled.get(),
        }
        if self.steps_position is not None:
            data["steps_panel"] = list(self.steps_position)
        path = self.settings_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass  # Settings still apply until the app closes.

    def width_choices(self, parent, card=False):
        frame = ttk.Frame(parent, style="Card.TFrame" if card else "TFrame")
        for text, value in WIDTHS:
            ttk.Radiobutton(frame, text=text, value=value, variable=self.control_width, command=self.set_control_width,
                            style="Card.TRadiobutton" if card else "TRadiobutton").pack(side="left", padx=(0, 10))
        return frame

    def width_value(self):
        return int(self.control_width.get().rstrip("%")) / 100

    def set_control_width(self):
        # The saved box stays as drawn; the width only changes how much of it spans the screen.
        if self.saved_reach is not None:
            self.saved_reach.width = self.width_value()
        self.save_settings()

    # Pages
    def show_page(self, name):
        self.page = name
        for key, page in self.pages.items():
            if key == name:
                page.grid()
            else:
                page.grid_remove()
        for key, label in self.step_labels.items():
            current = key == name
            label.configure(fg=ACCENT if current else MUTED,
                            font=theme.font(10, "bold") if current else theme.font(10))
        if name == "setup":
            self.refresh_setup_page()
        if name == "use":
            if self.two_hands.get():
                self.two_hand_guide.pack(anchor="w", pady=(8, 0))
            else:
                self.two_hand_guide.pack_forget()
        self.refresh_preview()

    def toggle_more_settings(self):
        if self.more_settings.winfo_ismapped():
            self.more_settings.pack_forget()
            self.more_button.configure(text="More settings ▸")
        else:
            self.more_settings.pack(fill="x")
            self.more_button.configure(text="More settings ▾")
        self.update_travel_options()

    def saved_boxes(self, camera):
        folder = profile_path(camera, 0).parent
        return [box for box in (load_reach(path) for path in folder.glob(f"reach-{camera}-*.json")) if box is not None]

    def has_calibration(self, camera):
        return bool(self.saved_boxes(camera))

    def refresh_setup_page(self):
        camera = int(self.camera.get())
        boxes = self.saved_boxes(camera)
        calibrated = bool(boxes)
        self.refinable = any(box.passes for box in boxes)
        if self.refinable:
            self.refine_link.pack(anchor="w", pady=(6, 0))
        else:
            self.refine_link.pack_forget()
        secondary = self.selected_second_camera()
        profile = load_profile(settings_path(camera))
        aligned = secondary is None or (profile is not None and profile.secondary == secondary)
        if calibrated and aligned:
            info = "Your calibration for this camera is saved. Choose Start. Refine it any time to make it steadier."
            primary = ("Start", self.start)
            alternate = ("Recalibrate", self.request_calibration)
        else:
            if calibrated:
                info = "The second camera needs lining up. Calibrating takes under a minute."
            elif secondary is not None:
                info = ("Calibrate once: turn both hands so the cameras line up, then draw your control box "
                        "in the air with both hands. Under a minute.")
            else:
                info = "Calibrate once by drawing your control box in the air with both hands. About half a minute."
            primary = ("Calibrate and start", self.request_calibration)
            alternate = ("Skip calibration", self.start)
        self.calibration_info.set(info)
        self.start_button.configure(text=primary[0], command=primary[1])
        self.alternate_button.configure(text=alternate[0], command=alternate[1])
        # Wait for a camera that is shutting down, or for a camera check's first frame.
        busy = self.session is not None and (self.session.stop.is_set() or self.last_frame is None)
        for button in (self.start_button, self.alternate_button):
            button.state(("disabled",) if busy else ("!disabled",))
        checking = self.session is not None and self.desktop is None
        self.test_button.configure(text="Stop check" if checking else "Check camera",
                                   command=self.pause if checking else self.test_camera)
        self.test_button.state(("disabled",) if self.session is not None and self.session.stop.is_set()
                               else ("!disabled",))
        idle = self.session is None
        for picker in (self.camera_picker, self.second_picker):
            picker.configure(state="readonly" if idle else "disabled")
        self.update_camera_mode()
        self.update_status_light()

    # Cameras
    def find_camera_names(self):
        if sys.platform != "win32":
            return
        result = []

        def lookup():
            try:
                from .windows_camera import camera_names
                result.append(camera_names())
            except Exception:
                result.append(None)

        Thread(target=lookup, daemon=True, name="camera names").start()

        def check():
            if not result:
                self.root.after(150, check)
            elif result[0]:
                self.camera_names = result[0]
                self.list_cameras()
        self.root.after(150, check)

    def camera_title(self, index):
        names = self.camera_names
        if names is None:
            return f"Camera {index}" + (" (usually built in)" if index == 0 else "")
        if index >= len(names):
            return f"Camera {index} (not connected)"
        name = names[index]
        return f"{name} ({index})" if names.count(name) > 1 else name

    def list_cameras(self):
        count = len(self.camera_names) if self.camera_names else 10
        primary = int(self.camera.get())
        indices = sorted(set(range(count)) | {primary})
        self.camera_choices = [(i, self.camera_title(i)) for i in indices]
        self.camera_picker.configure(values=[title for _, title in self.camera_choices])
        self.camera_label.set(self.camera_title(primary))
        second = self.selected_second_camera()
        self.second_choices = [(None, "None")] + [(i, title) for i, title in self.camera_choices if i != primary]
        if second is not None and second not in indices:
            self.second_choices.append((second, self.camera_title(second)))
        self.second_picker.configure(values=[title for _, title in self.second_choices])
        self.second_label.set("None" if second is None else self.camera_title(second))

    def choose_camera(self, event=None):
        title = self.camera_label.get()
        index = next(i for i, name in self.camera_choices if name == title)
        if str(index) != self.camera.get():
            self.camera.set(str(index))
            self.second_camera.set(self.saved_second_camera(index))
            self.reach_key = None
        self.list_cameras()
        self.save_settings()
        self.refresh_setup_page()

    def choose_second_camera(self, event=None):
        title = self.second_label.get()
        index = next(i for i, name in self.second_choices if name == title)
        self.second_camera.set(NO_SECOND if index is None else str(index))
        self.set_second_camera()
        self.save_settings()
        self.refresh_setup_page()

    def begin_calibration(self):
        """One guided pass. With a second camera, the turning steps also align the cameras."""
        if self.desktop is None or self.reach_key is None:
            return
        self.setup_open = True
        self.set_pad(False)
        self.show_page("calibrate")
        self.restart_button.state(("!disabled",))
        self.session.reach = None
        self.session.set_pointer_mode(self.gestures.click_gesture, DEFAULT_PALM_TRAVEL)
        self.root.deiconify()
        self.root.lift()
        self.close_camera_settings()
        self.end_summary(force=True)
        self.set_status(CALIBRATING)
        mode, face = self.calibration_mode
        previous = self.saved_reach if mode != "fresh" and self.saved_reach is not None else None
        if previous is None or not previous.passes:
            mode, face, previous = "fresh", None, None
        stereo = self.session.secondary is not None
        # Refining keeps cameras that are already lined up. A fresh calibration lines them up again.
        align = stereo and (mode == "fresh" or self.session.stereo_profile is None)
        if align:
            # Depth from an earlier alignment would mix with the new one, so pause it.
            if self.session.stereo_profile is not None:
                self.previous_profile = self.session.stereo_profile
            self.session.stereo_profile = None
        # Both hands draw the box, so follow each of them on every frame.
        self.session.set_two_hands(True)
        self.session.set_eager_second(True)
        self.capture = BoxCalibration(stereo=stereo, align=align, faces=(face,) if mode == "redo" else FACES,
                                      previous=previous, mode=mode)
        self.calibration_heading.set(self.heading(mode, face))
        self.calibration_step = "guided"
        self.calibration_view = None
        self.pair_frames = ([], [])
        self.pair_fit = None
        self.pair_problem = ""
        self.clear_pair_previews()
        self.cube.reset()
        self.update_stereo_label()
        self.show_steps()
        self.draw_step()

    def clear_pair_previews(self):
        """Empty both camera views. A label keeps naming its picture after the picture is gone,
        and Tk then refuses every later change to that label, so clear the labels first."""
        for label, caption, name in zip(self.pair_labels, self.pair_captions, ("Main camera", "Second camera")):
            label.configure(image="", text="Starting…")
            caption.configure(text=name)
        self.pair_images = [None, None]

    def collect_pair(self, sample):
        """Gather both camera views while you turn your hand, for the alignment fit."""
        aux = self.session.aux_camera
        if aux is None:
            self.pair_problem = f"Starting {self.camera_title(self.session.secondary)}…"
            return
        if aux.error:
            self.pair_problem = f"{self.camera_title(aux.camera_index)}: {aux.error} Pick another second camera."
            return
        self.pair_problem = ""
        aux.set_preview(True)
        other = aux.take_sample()
        for index, frame in ((0, sample), (1, other)):
            if frame is not None and frame.preview is not None:
                # Show the new picture before letting go of the old one, which Tk deletes with it.
                image = tk.PhotoImage(data=frame.preview, format="ppm")
                self.pair_labels[index].configure(image=image, text="")
                self.pair_images[index] = image
            if frame is not None:
                name = ("Main camera", "Second camera")[index]
                self.pair_captions[index].configure(text=f"{name} · open hand found" if open_hand(frame) else name)
        for frames, view in zip(self.pair_frames, (self.session.recent_samples(), aux.recent_samples())):
            last = frames[-1].timestamp if frames else float("-inf")
            frames.extend(same_hand_first(f) for f in view if f.points is not None and f.timestamp > last)

    def check_alignment(self):
        if self.pair_fit is None:
            result = []

            def fit(first, second, secondary):
                try:
                    result.append(solve_video_tracks(first, second, secondary, both_hands=True))
                except Exception as exc:  # A failed fit only means more turning is needed.
                    result.append(exc)

            views = tuple(tuple(frames) for frames in self.pair_frames)
            Thread(target=fit, args=(*views, self.session.secondary), daemon=True, name="stereo alignment").start()
            self.pair_fit = result
            return
        if not self.pair_fit:
            return
        outcome, self.pair_fit = self.pair_fit[0], None
        now = monotonic()
        if isinstance(outcome, Exception):
            message = str(outcome) if isinstance(outcome, ValueError) else "The views didn't line up."
            self.capture.alignment_done(False, now, message)
            return
        profile = outcome
        path = settings_path(self.session.camera_index)
        saved = load_profile(path)
        if saved is not None and saved.secondary == profile.secondary:
            try:
                profile = average_profiles(saved, profile)
            except ValueError:
                pass  # A camera moved, so this run starts a fresh alignment.
        try:
            save_profile(path, profile)
        except OSError:
            pass  # The alignment still applies for this session.
        self.session.stereo_profile = profile
        self.previous_profile = None
        self.capture.alignment_done(True, now)

    def stop_pair_preview(self):
        if self.session is not None and self.session.aux_camera is not None:
            self.session.aux_camera.set_preview(False)

    def restore_hands(self):
        if self.session is not None:
            self.session.set_eager_second(False)
            self.session.set_two_hands(self.two_hands.get())

    @staticmethod
    def heading(mode, face):
        if mode == "refine":
            return "Refine your control box"
        if mode == "redo":
            return f"Redo one side: {FACE_TITLES[face]}"
        return "Draw your control box"

    def request_calibration(self, mode="fresh", face=None):
        """Calibrate from scratch, refine the saved box with another drawing, or redo one side of it."""
        if self.setup_open:
            return
        self.calibration_mode = (mode, face) if mode in ("refine", "redo") else ("fresh", None)
        self.calibration_origin = "setup" if self.desktop is None else "use"
        if self.desktop is None:
            self.start()
            if self.desktop is None:
                return
            self.calibrate_on_start = True
            self.show_waiting()
        elif self.last_frame is None or self.reach_key is None:
            self.calibrate_on_start = True
            self.show_waiting()
        else:
            self.begin_calibration()

    def show_waiting(self):
        """The calibration page and steps panel while the camera starts."""
        self.close_camera_settings()
        self.end_summary(force=True)
        self.calibration_heading.set(self.heading(*self.calibration_mode))
        self.show_page("calibrate")
        self.restart_button.state(("disabled",))
        self.calibration_view = None
        self.clear_pair_previews()
        self.show_view("cube")
        self.cube.reset()
        self.update_stereo_label()
        self.show_steps()
        self.update_waiting()

    def update_waiting(self):
        mode, face = self.calibration_mode
        session = self.session
        stereo = session is not None and session.secondary is not None
        align = stereo and (mode == "fresh" or session.stereo_profile is None)
        plan = calibration_plan(align, (face,) if mode == "redo" else FACES)
        kicker = {"refine": "Getting ready to refine", "redo": "Getting ready to redo a side"}.get(mode, "Getting ready")
        connection = session.connection_status if session is not None else ""
        self.steps_panel().update(kicker, "Starting the camera…", WAITING,
                                  connection if connection != "Starting camera…" else "", 0.0, None, plan[:3])

    def calibrate_menu_from_setup(self):
        """The calibrate menu, opened from the setup page's link."""
        self.update_calibrate_menu(self.refinable)
        link = self.refine_link
        self.calibrate_menu.tk_popup(link.winfo_rootx(), link.winfo_rooty() + link.winfo_height())

    def update_calibrate_menu(self, refinable):
        state = "normal" if refinable else "disabled"
        self.calibrate_menu.entryconfigure(0, state=state)
        self.calibrate_menu.entryconfigure(1, state=state)

    # The steps panel
    def steps_panel(self):
        if self.steps is None:
            self.steps = StepsPanel(self.root, on_move=self.remember_steps_position)
            self.steps.position = self.steps_position
        return self.steps

    def show_steps(self):
        if self.page != "calibrate":
            return
        layout = self.desktop.layout.rects if self.desktop is not None else ()
        self.root.update_idletasks()
        self.steps_panel().show(self.root, layout)

    def hide_steps(self):
        if self.steps is not None:
            self.steps.hide()

    def remember_steps_position(self, position):
        self.steps_position = position
        self.save_soon()

    def save_soon(self):
        """Save settings once changes settle, rather than on every small move."""
        if self.save_job is not None:
            self.root.after_cancel(self.save_job)
        self.save_job = self.root.after(700, self.saved_soon)

    def saved_soon(self):
        self.save_job = None
        self.save_settings()

    def cancel_calibration(self):
        if self.session is not None and self.session.stereo_profile is None and self.previous_profile is not None:
            self.session.stereo_profile = self.previous_profile
        self.previous_profile = None
        if self.calibration_origin == "setup":
            self.pause("Calibration cancelled. Camera is off.")
        else:
            self.finish_setup()
            self.set_status("Calibration cancelled. Your previous setup is in use.")

    def draw_step(self):
        capture = self.capture
        # Show both camera views while turning with two cameras; otherwise the reach cube.
        view = "pair" if capture.collecting_pair else "cube"
        if view != self.calibration_view:
            self.show_view(view)
            if view == "cube":
                self.stop_pair_preview()
            self.refresh_preview()
        if view == "cube":
            self.cube.draw(capture)
        self.steps_panel().update(capture.kicker(), capture.headline(), capture.instruction(),
                                  self.pair_problem or capture.hint, capture.progress,
                                  capture.plan[capture.step_index()], capture.upcoming())

    def show_view(self, view):
        """Swap the box for both camera views inside the same fixed-size frame."""
        self.calibration_view = view
        shown, hidden = (self.pair_previews, self.cube) if view == "pair" else (self.cube, self.pair_previews)
        hidden.place_forget()
        shown.place(relx=0.5, rely=0.5, anchor="center")

    def finish_setup(self, keep_steps=False):
        self.stop_pair_preview()
        self.restore_hands()
        if not keep_steps:
            self.hide_steps()
        self.setup_open = False
        self.capture = self.alignment = self.calibration_step = self.calibration_view = None
        self.pair_problem = ""
        self.clear_pair_previews()
        self.calibrate_on_start = False
        self.apply_reach()
        self.show_page("use" if self.desktop is not None else "setup")
        self.update_stereo_label()
        self.set_status("Tracking. Show your aiming hand.")

    def apply_reach(self):
        self.gestures.reach = self.saved_reach if self.reach_enabled.get() else None
        self.update_calibrate_menu(self.saved_reach is not None and bool(self.saved_reach.passes))
        if self.session is not None:
            self.session.reach = self.gestures.reach
        self.update_travel_options()
        self.reset_pointer_mode()

    def observe_setup(self, sample):
        capture = self.capture
        if capture is None:
            return
        hands = []
        for points, depth in ((sample.points, sample.depth_m), (sample.second, sample.second_depth_m)):
            if points is not None:
                x, y = aim_landmark(points, self.gestures.click_gesture)
                hands.append((float(x / max(1, sample.width - 1)), float(y / max(1, sample.height - 1)),
                              palm_scale(points, sample.width), depth))
        features = hand_features(sample) if sample.points is not None and open_hand(sample) else None
        if capture.collecting_pair:
            self.collect_pair(sample)
        capture.observe(hands, sample.timestamp, features)
        if capture.stage == "aligning":
            self.check_alignment()
        if capture.result is None:
            self.draw_step()
            return
        self.saved_reach = capture.result
        self.saved_reach.width = self.width_value()
        self.reach_enabled.set(True)
        summary = self.refine_summary(capture)
        self.finish_setup(keep_steps=summary is not None)
        self.feedback = summary["status"] if summary else "Calibration saved."
        try:
            save_reach(profile_path(*self.reach_key), self.saved_reach)
        except OSError:
            self.feedback = "Calibration is active for this session. The settings file could not be saved."
        self.feedback_until = monotonic() + 5
        self.set_status(self.feedback)
        if summary:
            self.show_summary(summary)

    # After refining: how far the corners moved
    def refine_summary(self, capture):
        if capture.mode == "fresh" or not capture.changes:
            return None
        moves = [change[2] * 100 for change in capture.changes]
        average, largest = sum(moves) / len(moves), moves[0]
        if capture.mode == "refine":
            kicker = f"Refined · {len(capture.result.passes)} drawings blended"
            headline = "Box refined"
            body = (f"Corners moved {average:.1f}% of the screen on average and {largest:.1f}% at most. "
                    "Refine again any time to make it steadier.")
            feedback = ("That's a big change. If the camera or your seat moved, calibrate from scratch instead."
                        if largest > 6 else "Small changes mean your box was already accurate." if largest < 1.5
                        else "")
        else:
            side = FACE_TITLES[capture.face_list[0]]
            kicker = f"Redone · {side}"
            headline = f"{side} side redone"
            body = (f"Its corners moved {average:.1f}% of the screen on average and {largest:.1f}% at most. "
                    "The other sides stayed as they were.")
            feedback = ""
        rows = [(("corner", (face, key)), f"{CORNER_NAMES[key]} · moved {move * 100:.1f}%", FACE_TITLES[face])
                for face, key, move in capture.changes[:3]]
        return {"kicker": kicker, "headline": headline, "body": body, "feedback": feedback, "rows": rows,
                "status": f"{headline}: corners moved {average:.1f}% on average, {largest:.1f}% at most."}

    def show_summary(self, summary):
        """Keep the steps panel up a little longer to show what changed."""
        panel = self.steps_panel()
        panel.update(summary["kicker"], summary["headline"], summary["body"], summary["feedback"], 1.0, DONE_STEP, (),
                     rows=summary["rows"])
        if not panel.shown:
            panel.show(self.root, self.desktop.layout.rects if self.desktop is not None else ())
        self.summary_job = self.root.after(20000, self.end_summary)

    def end_summary(self, force=False):
        if self.summary_job is not None:
            self.root.after_cancel(self.summary_job)
            self.summary_job = None
        if force:
            return
        if not (self.setup_open or self.page == "calibrate"):
            self.hide_steps()

    def run_gesture(self, gesture, now, direction=None):
        """Run whatever action is assigned to a gesture."""
        action = self.gesture_actions.get(gesture, "nothing")
        shortcut = self.gesture_shortcuts.get(gesture)
        if action == "nothing":
            return False
        if action == "switch_screen":
            direction = direction or DIRECTIONS.get(gesture)
            return direction is not None and self.switch_screen(direction, now)
        if action == "number_pad":
            self.set_pad(not self.pad_enabled, by_gesture=True)
            return True
        if action == "pause":
            self.pause("Paused by gesture. The camera is off.")
            return True
        try:
            keys = keys_for(action, shortcut)
            if keys:
                self.desktop.press_keys(keys)
            self.feedback = f"{GESTURE_NAMES[gesture]}: {describe(action, shortcut)}"
        except Exception as exc:  # A failed key press shouldn't stop tracking.
            self.feedback = f"Couldn't send {describe(action, shortcut)}: {exc}"
        self.feedback_until = now + 1.4
        self.set_status(self.feedback)
        return True

    def switch_screen(self, direction, now):
        if not self.desktop.switch_monitor(direction):
            return False
        self.release_pointer()
        self.show_display_count()
        self.feedback = f"Now controlling the screen to the {direction}." if direction in ("left", "right") else (
            f"Now controlling the screen {'above' if direction == 'up' else 'below'}.")
        self.feedback_until = now + 1.4
        self.set_status(self.feedback)
        return True

    def edge_switch(self, gesture, now):
        """Reach past the side of your box and hold for a moment to control the screen that way."""
        self.edge_message = ""
        layout = self.desktop.layout
        if (self.gesture_actions["edge_push"] != "switch_screen" or len(layout.rects) < 2
                or gesture.raw_pointer is None or gesture.aim_held):
            self.edge.reset()
            return False
        active = self.desktop.active_monitor
        available = {direction for direction in ("left", "right", "up", "down")
                     if layout.adjacent(active, direction) is not None}
        fired, pending, _ = self.edge.update(gesture.raw_pointer, now, available)
        if fired is not None:
            return self.switch_screen(fired, now)
        if pending is not None:
            where = {"left": "to the left", "right": "to the right", "up": "above", "down": "below"}[pending]
            self.edge_message = f"Keep reaching to control the screen {where}…"
        return False

    def update_gesture_guide(self):
        names = {gesture: describe(self.gesture_actions[gesture], self.gesture_shortcuts.get(gesture))
                 for gesture in GESTURE_NAMES}
        self.palm_action.set(names["palm_hold"] if self.gesture_actions["palm_hold"] != "nothing" else "Nothing")
        if self.fist_enabled.get():
            self.fist_guide.set(
                f"Make a fist and swing it: ← {names['swing_left']} · → {names['swing_right']} · "
                f"↑ {names['swing_up']} · ↓ {names['swing_down']}. Push it toward the screen: {names['push']}. "
                f"Pull it back: {names['pull']}.")
        else:
            self.fist_guide.set("Fist gestures are off. Turn them on in Gestures….")
        ways = []
        if self.gesture_actions["edge_push"] == "switch_screen":
            ways.append("reach past the side of your box toward it and hold for a moment")
        if self.gesture_actions["flick"] == "switch_screen":
            ways.append("flick your pointing hand sharply toward it")
        self.screen_guide.set(f"To control another screen, {' or '.join(ways)}." if ways
                              else "Screen switching gestures are off.")

    def gestures_changed(self):
        self.fists.reset()
        self.edge.reset()
        self.update_gesture_guide()
        if self.desktop is not None:
            self.show_display_count()
        self.save_settings()

    def customize_gestures(self):
        """A small window to choose what each gesture does."""
        if self.customize_window is not None and self.customize_window.winfo_exists():
            self.customize_window.deiconify()
            self.customize_window.lift()
            return
        window = self.customize_window = tk.Toplevel(self.root)
        window.title("Customize gestures")
        window.configure(bg=PAPER)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=(20, 16, 20, 16))
        frame.grid()
        tk.Label(frame, text="Customize gestures", bg=PAPER, fg=INK, font=theme.font(16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        tk.Label(frame, text="Choose what each gesture does. Fist gestures work in every control mode, and a fist "
                             "never moves the pointer or clicks.", bg=PAPER, fg=MUTED, wraplength=520,
                 justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 8))
        ttk.Checkbutton(frame, text="Use fist gestures", variable=self.fist_enabled,
                        command=self.gestures_changed).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 6))
        self.customize_rows = {}
        for row, (gesture, label) in enumerate(GESTURES, start=3):
            tk.Label(frame, text=label, bg=PAPER, fg=INK).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=3)
            choices = available_actions(gesture)
            choice = tk.StringVar(value=ACTIONS[self.gesture_actions[gesture]])
            picker = ttk.Combobox(frame, textvariable=choice, values=[ACTIONS[action] for action in choices],
                                  state="readonly", width=30)
            picker.grid(row=row, column=1, sticky="w", pady=3)
            shortcut = tk.StringVar(value=self.gesture_shortcuts.get(gesture, ""))
            entry = ttk.Entry(frame, textvariable=shortcut, width=16)
            entry.grid(row=row, column=2, sticky="w", padx=(8, 0))
            if self.gesture_actions[gesture] != "custom":
                entry.grid_remove()
            picker.bind("<<ComboboxSelected>>", lambda _event, name=gesture: self.choose_gesture_action(name))
            entry.bind("<Return>", lambda _event, name=gesture: self.set_gesture_shortcut(name))
            entry.bind("<FocusOut>", lambda _event, name=gesture: self.set_gesture_shortcut(name, quiet=True))
            self.customize_rows[gesture] = choice, choices, shortcut, entry
        self.customize_message = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.customize_message, bg=PAPER, fg=ACCENT, wraplength=520,
                 justify="left").grid(row=len(GESTURES) + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(GESTURES) + 4, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Done", style="Primary.TButton", command=window.destroy).pack(side="left")
        ttk.Button(buttons, text="Reset to defaults", command=self.reset_gestures).pack(side="left", padx=(8, 0))

    def choose_gesture_action(self, gesture):
        choice, choices, shortcut, entry = self.customize_rows[gesture]
        action = choices[[ACTIONS[item] for item in choices].index(choice.get())]
        if action == "custom":
            # Keep the current action until a valid shortcut is typed.
            entry.grid()
            entry.focus_set()
            self.customize_message.set("Type a shortcut like Ctrl+Shift+T, then press Enter.")
            return
        entry.grid_remove()
        self.gesture_actions[gesture] = action
        self.gesture_shortcuts.pop(gesture, None)
        self.customize_message.set(f"{GESTURE_NAMES[gesture]}: {ACTIONS[action]}")
        self.gestures_changed()

    def set_gesture_shortcut(self, gesture, quiet=False):
        choice, choices, shortcut, entry = self.customize_rows[gesture]
        text = shortcut.get().strip()
        if not text:
            return
        try:
            formatted = format_shortcut(parse_shortcut(text))
        except ValueError as exc:
            if not quiet:
                self.customize_message.set(str(exc))
            return
        shortcut.set(formatted)
        choice.set(ACTIONS["custom"])
        self.gesture_actions[gesture] = "custom"
        self.gesture_shortcuts[gesture] = formatted
        self.customize_message.set(f"{GESTURE_NAMES[gesture]}: {formatted}")
        self.gestures_changed()

    def reset_gestures(self):
        self.gesture_actions, self.gesture_shortcuts = clean_choices({}, {})
        self.fist_enabled.set(True)
        for gesture, (choice, choices, shortcut, entry) in self.customize_rows.items():
            choice.set(ACTIONS[self.gesture_actions[gesture]])
            shortcut.set("")
            entry.grid_remove()
        self.customize_message.set("Back to the default gestures.")
        self.gestures_changed()

    def set_status(self, text):
        if self.status.get() != text:
            self.status.set(text)
        self.update_status_light()
        self.update_camera_mode()

    def update_status_light(self):
        """Green while tracking, amber while starting or calibrating, red on a camera error, grey when off."""
        light = getattr(self, "status_light", None)
        if light is None:
            return
        session = self.session
        if self.copy_error_button.winfo_manager():
            color = theme.DANGER
        elif session is None or session.stop.is_set():
            color = theme.DIM
        elif self.setup_open or self.page == "calibrate" or self.last_frame is None:
            color = ACCENT
        else:
            color = theme.SUCCESS
        if getattr(self, "light_color", None) != color:
            light.itemconfigure("light", fill=color)
            self.light_color = color

    def update_camera_mode(self):
        """The header chip and the setup hint: one camera, or two cameras measuring depth."""
        if getattr(self, "camera_chip", None) is None:
            return
        if self.selected_second_camera() is None:
            chip = ("\u25c9  One camera", MUTED, theme.FIELD)
            hint = ("Add a second webcam for true 3D depth: steadier aim, cleaner clicks, and a box that fits "
                    "your reach.", ACCENT)
        else:
            session = self.session
            depth = (session is not None and not session.stop.is_set() and session.secondary is not None
                     and session.stereo_profile is not None)
            chip = ("\u25c9\u25c9  3D depth on", theme.ON_ACCENT, ACCENT) if depth else (
                "\u25c9\u25c9  Two cameras", ACCENT, theme.FIELD)
            hint = ("Two cameras: depth comes from both views. Calibrating lines them up.", theme.SUCCESS)
        if self.camera_mode.get() != chip[0]:
            self.camera_mode.set(chip[0])
            self.camera_chip.configure(fg=chip[1], bg=chip[2])
        if self.second_hint.get() != hint[0]:
            self.second_hint.set(hint[0])
            self.second_hint_label.configure(fg=hint[1])

    def set_response(self, _event):
        self.gestures.response = self.response.get()

    def set_sticky(self):
        self.sticky.reset()
        if self.sticky_enabled.get() and self.desktop is not None and self.sticky_targets is None:
            from .targets_windows import WindowsTargets

            self.sticky_targets = WindowsTargets()

    def update_click_indicator(self):
        if not self.show_clicks.get() and self.click_indicator is not None:
            self.click_indicator.hide()

    def indicate_click(self, button):
        if not self.show_clicks.get():
            return
        try:
            if self.click_indicator is None:
                from .click_feedback import WindowsClickFeedback

                self.click_indicator = WindowsClickFeedback(self.root)
            pixel = self.desktop.pixel_position()
            self.click_indicator.show(button, pixel, self.desktop.layout.monitor_at(*pixel))
        except (OSError, tk.TclError):
            pass  # Optional feedback must not interrupt a mouse press.

    def set_drag(self):
        self.gestures.drag_enabled = self.drag_enabled.get()
        self.reset_pointer_mode()
        self.update_guide()

    def start_hold(self):
        """The click gesture closed: press the button where the pointer is and keep it down."""
        if self.last_pointer is None:
            self.gestures.cancel_drag()
            return
        self.press_pixel = self.desktop.pointer_pixel(self.last_pointer)
        self.desktop.hold("left", target=self.last_pointer)
        self.holding, self.dragged = True, False

    def drag_pointer(self, motion, hand):
        left, top, right, bottom = self.desktop.active_monitor
        x = self.press_pixel[0] + motion[0] * (right - left - 1)
        y = self.press_pixel[1] + motion[1] * (bottom - top - 1)
        # Reaching the edge of your box reaches the edge of the screen, to snap a window or open an edge menu.
        if hand[0] <= 0.0 or hand[0] >= 1.0:
            x = left if hand[0] <= 0.0 else right - 1
        if hand[1] <= 0.0 or hand[1] >= 1.0:
            y = top if hand[1] <= 0.0 else bottom - 1
        return self.desktop.layout.to_pointer(min(max(round(x), left), right - 1), min(max(round(y), top), bottom - 1))

    def finish_hold(self, now):
        """The grip opened: let the button up where the pointer is."""
        if not self.holding:
            return
        self.holding = False
        self.press_pixel = None
        self.desktop.release_hold(target=self.last_pointer)
        self.feedback = "Dropped." if self.dragged else "Click sent."
        self.feedback_until = now + 0.35
        if self.last_pointer is not None:
            self.sticky.place(self.last_pointer)

    def release_pointer(self):
        if self.holding:
            # Never leave a mouse button down when the pointer is let go for any reason.
            self.holding = False
            self.press_pixel = None
            self.gestures.cancel_drag()
            if self.desktop is not None:
                self.desktop.release_hold(target=self.last_pointer)
        self.sticky.reset()
        self.last_pointer = None

    def aim_pointer(self, pointer, now):
        unchanged = pointer == self.last_pointer
        if unchanged and now < self.next_pointer_check:
            return
        self.next_pointer_check = now + 0.15
        if unchanged:
            # Recover when a physical mouse or another app moved the cursor while the hand was still.
            clicks = self.desktop.clicks
            if clicks.pressed_button is not None or clicks.pending or self.desktop.at_pointer(pointer):
                return
        self.desktop.move(pointer)
        self.last_pointer = pointer

    def set_click_gesture(self, _event):
        gesture = self.click_gesture.get()
        self.gestures.click_gesture = gesture
        self.update_travel_options()
        self.reset_pointer_mode()
        self.update_guide()

    def update_guide(self):
        mode = self.gestures.click_gesture
        actions = ("Touch thumb to middle fingertip", "Two fingers, move up or down", "Three fingers, move up or down")
        if mode == RELAXED_HAND:
            actions = ("Curl middle, ring and pinky into palm", "Tuck thumb, two fingers up, move vertically",
                       "Tuck thumb, three fingers up, move vertically")
        for variable, instruction in zip(self.instructions, (*CLICK_GUIDE[mode], *actions)):
            variable.set(instruction)
        self.drag_instruction.set(DRAG_GUIDE[mode] if self.drag_enabled.get() or mode == "Air tap"
                                  else "Turn on Hold to drag in More settings")
        self.mode_hint.set(MODE_HINTS[mode])

    def update_travel_options(self):
        if self.gestures.click_gesture in PALM_MODES and not self.reach_enabled.get():
            self.travel_options.grid()
        else:
            self.travel_options.grid_remove()

    def set_palm_travel(self, _event):
        self.gestures.palm_travel = self.palm_travel.get()
        self.reset_pointer_mode()

    def reset_pointer_mode(self):
        self.gestures.reset(keep_pointer=True)
        if self.desktop is not None:
            self.desktop.cancel_clicks()
        if self.session is not None:
            self.session.set_pointer_mode(self.gestures.click_gesture, self.gestures.palm_travel)
        self.release_pointer()
        self.feedback_until = 0.0

    def set_two_hands(self):
        enabled = self.two_hands.get()
        if enabled and self.page == "use":
            self.two_hand_guide.pack(anchor="w", pady=(8, 0))
        else:
            self.two_hand_guide.pack_forget()
        if self.session is not None:
            self.session.set_two_hands(enabled)
        self.gestures.reset(keep_pointer=True)
        self.release_pointer()

    def start(self):
        if self.desktop is not None or (self.session is not None and
                                        (self.session.stop.is_set() or self.last_frame is None)):
            return
        try:
            self.desktop = open_desktop(self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        except Exception as exc:
            self.set_status("Desktop control is unavailable.")
            messagebox.showerror("MimeControl", str(exc), parent=self.root)
            return
        self.set_sticky()
        self.save_settings()
        if sys.platform == "win32":
            self.desktop.clicks.on_press = self.indicate_click
        self.show_display_count()
        self.next_display_check = monotonic() + 1.5
        if self.session is None:
            self.open_camera(tracking=True)
        else:
            # Continue straight from a camera check with the same video.
            secondary = self.selected_second_camera()
            self.session.use_secondary(secondary, self.matching_profile(secondary))
            self.session.tracking.set()
            self.show_stereo_hint()
        self.testing = False
        self.show_page("use")
        self.set_status("Starting tracking…")

    def test_camera(self, with_second=False):
        if self.session is not None:
            return
        self.testing = True
        self.open_camera(tracking=False, with_second=with_second)
        self.refresh_setup_page()
        self.refresh_preview()

    # Camera settings
    def open_camera_settings(self):
        """The webcam's own picture settings. Starts a camera check first if the camera is off."""
        if self.setup_open or self.page == "calibrate":
            return
        if self.camera_window is not None:
            self.camera_window.window.deiconify()
            self.camera_window.window.lift()
            return
        if self.session is None:
            self.test_camera(with_second=True)
        elif self.session.stop.is_set():
            return
        from .camera_settings import CameraSettingsWindow

        self.camera_window = CameraSettingsWindow(self.root, self.camera_sessions, self.remember_camera_setting,
                                                  self.camera_settings_closed, idle_message=self.status.get)
        if self.settings_only:
            self.camera_window.window.geometry("+80+60")
        self.refresh_preview()

    def camera_sessions(self):
        session = self.session
        if session is None or session.stop.is_set():
            return []
        cameras = [(self.camera_title(session.camera_index), session)]
        aux = session.aux_camera
        if aux is not None and not aux.stop.is_set():
            cameras.append((self.camera_title(aux.camera_index), aux))
        return cameras

    def remember_camera_setting(self, name, key, choice):
        if not name:
            return
        choices = dict(self.camera_settings.get(name, {}))
        if choice is None:
            choices.pop(key, None)
        else:
            choices[key] = choice
        if choices:
            self.camera_settings[name] = choices
        else:
            self.camera_settings.pop(name, None)
        if self.session is not None:
            self.session.remember_controls(name, choices)
        self.save_soon()

    def close_camera_settings(self):
        if self.camera_window is not None:
            self.camera_window.close()

    def camera_settings_closed(self):
        self.camera_window = None
        self.save_settings()
        if self.settings_only:
            self.close()
            return
        self.refresh_preview()

    def set_camera_filter(self, event=None):
        if self.session is not None:
            self.session.camera_filter = self.camera_filter.get()

    @staticmethod
    def saved_second_camera(primary):
        profile = load_profile(settings_path(primary))
        if profile is None or not profile.enabled or profile.secondary == primary:
            return NO_SECOND
        return str(profile.secondary)

    def selected_second_camera(self):
        choice = self.second_camera.get()
        if not choice.isdigit() or int(choice) == int(self.camera.get()):
            return None
        return int(choice)

    def set_second_camera(self, event=None):
        # Remember the choice with a saved alignment for the same pair.
        path = settings_path(int(self.camera.get()))
        profile = load_profile(path)
        secondary = self.selected_second_camera()
        if profile is not None and (secondary is None or secondary == profile.secondary):
            try:
                save_profile(path, replace(profile, enabled=secondary is not None))
            except OSError:
                pass  # The choice still applies until the app closes.

    def open_camera(self, tracking, with_second=False):
        self.copy_error_button.grid_remove()
        self.copy_error_button.configure(text="Copy camera error")
        self.error_details = ""
        primary = int(self.camera.get())
        secondary = self.selected_second_camera() if tracking or with_second else None
        profile = self.matching_profile(secondary) if tracking else None
        self.session = CameraSession(
            primary, self.preview_enabled.get(), tracking=tracking, two_hands=self.two_hands.get(), stereo_profile=profile,
            mode=self.gestures.click_gesture, palm_travel=self.gestures.palm_travel, camera_filter=self.camera_filter.get(),
            secondary=secondary, control_names=self.control_cameras, saved_controls=self.camera_settings,
        )
        if with_second and not tracking:
            self.session.start_second_camera()  # A camera check for settings shows both cameras.
        self.session.reach = self.gestures.reach if tracking else None
        self.show_stereo_hint()
        self.set_status("Starting camera…")
        self.root.after(0, self.poll)

    def matching_profile(self, secondary):
        profile = load_profile(settings_path(int(self.camera.get())))
        return profile if profile is not None and secondary is not None and profile.secondary == secondary else None

    def show_stereo_hint(self):
        secondary = self.session.secondary if self.session is not None else None
        if secondary is not None and self.session.stereo_profile is None and self.session.tracking.is_set():
            self.stereo_info.set(f"{self.camera_title(secondary)} isn't aligned yet. Recalibrate to use it.")
            self.update_stereo_label()

    def pause(self, message="Paused. The camera is off."):
        if self.session is None or self.session.stop.is_set():
            return
        self.session.stop.set()
        self.calibrate_on_start = False
        if self.setup_open:
            self.reach_key = None
            self.finish_setup()
        self.hide_steps()
        if self.click_indicator is not None:
            self.click_indicator.hide()
        if self.desktop is not None:
            try:
                self.desktop.stop()
            except Exception:
                pass  # Tracking and input still stop if the desktop connection is already gone.
            self.desktop = None
        self.display_info.set("")
        self.swipe_guide.grid_remove()
        self.set_pad(False)
        self.gestures.reset()
        self.last_frame = None
        self.testing = False
        self.clear_preview()
        self.show_page("setup")
        self.set_status(message)

    def close(self):
        if self.camera_window is not None:
            window, self.camera_window = self.camera_window, None
            window.on_close = lambda: None
            window.close()
        self.hide_steps()
        self.save_settings()
        if self.click_indicator is not None:
            self.click_indicator.close()
            self.click_indicator = None
        if self.sticky_targets is not None:
            self.sticky_targets.close()
            self.sticky_targets = None
        if self.session is None:
            self.root.destroy()
            return
        self.closing = True
        self.pause()
        self.root.withdraw()
        self.root.after(1200, self.root.destroy)

    def set_pad(self, enabled, by_gesture=False):
        if enabled and (self.desktop is None or self.setup_open):
            return
        self.pad_enabled = enabled
        if self.session is not None:
            self.session.set_number_pad(enabled)
        if enabled and self.pad is None:
            self.pad = NumberPad(self.root)
        if self.pad is not None:
            self.pad.show(self.pointer_monitor() if enabled else None)
        self.pad_button.configure(text="Close number pad" if enabled else "Number pad")
        self.release_pointer()
        self.feedback_until = 0.0
        if self.desktop is not None:
            self.desktop.cancel_clicks()
        self.gestures.reset(keep_palm=by_gesture, keep_pointer=True)

    def pointer_monitor(self):
        layout = self.desktop.layout
        return layout.monitor_at(*layout.nearest_pixel(*self.desktop.pixel_position()))

    def show_display_count(self):
        layout = self.desktop.layout
        count = len(layout.rects)
        self.gestures.swipe_enabled = count > 1 and self.gesture_actions["flick"] == "switch_screen"
        if count > 1:
            self.swipe_guide.grid()
        else:
            self.swipe_guide.grid_remove()
        if count == 1:
            self.display_info.set("1 display")
        else:
            # Number screens left to right, then top to bottom, the way they're arranged.
            order = sorted(layout.rects, key=lambda rect: (rect[0], rect[1]))
            active = self.desktop.active_monitor
            number = order.index(active) + 1 if active in order else 1
            self.display_info.set(f"{count} displays · using {number}")

    def refresh_displays(self):
        now = monotonic()
        if now < self.next_display_check:
            return
        self.next_display_check = now + 1.5
        if self.desktop.refresh_layout():
            self.gestures.reset()
            self.release_pointer()
            self.feedback_until = 0.0
            if self.pad_enabled:
                self.pad.show(self.pointer_monitor())
            self.show_display_count()

    def copy_camera_error(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.error_details or self.status.get())
        self.copy_error_button.configure(text="Copied")

    def clear_preview(self, message="Starting camera…"):
        self.preview_label.configure(image="", text=message)
        self.image = None

    def refresh_preview(self, event=None):
        if event is not None and event.widget != self.root:
            return
        shown = (self.page == "setup" and self.testing) or (self.page == "use" and self.preview_enabled.get())
        if shown:
            self.preview_label.grid()
        else:
            self.preview_label.grid_remove()
            self.clear_preview()
        if self.session is not None:
            settings = self.camera_window is not None and self.camera_window.session is self.session
            wanted = shown or (self.setup_open and self.calibration_view == "pair")
            self.session.set_preview(settings or (wanted and self.root.state() == "normal"))

    def poll(self):
        session = self.session
        if not session.thread.is_alive():
            self.pause(session.error or "Paused. The camera is off.")
            self.session = None
            if self.closing:
                self.root.destroy()
                return
            if session.error:
                self.error_details = ""
                self.copy_error_button.configure(text="Copy camera error")
                self.copy_error_button.grid()
            self.stereo_info.set("")
            self.update_stereo_label()
            self.refresh_setup_page()
            return
        if session.stop.is_set():
            self.root.after(40, self.poll)
            return

        try:
            if session.stereo_status and session.stereo_status != self.stereo_info.get():
                self.stereo_info.set(session.stereo_status)
                self.update_stereo_label()
            self.learn_control_cameras(session)
            if self.calibrate_on_start and self.page == "calibrate":
                self.update_waiting()
            if self.desktop is not None:
                self.refresh_displays()
            commands = tuple(self.desktop.take_commands()) if self.desktop is not None else ()
            if "pause" in commands:
                self.pause()
            elif "calibrate" in commands:
                self.request_calibration()
            elif "pad" in commands:
                self.set_pad(not self.pad_enabled)
            if not session.stop.is_set():
                if session.backend is None:
                    self.set_status(session.connection_status)
                self.use_sample(session.take_sample())
        except Exception as exc:
            details = traceback.format_exc()
            self.record_error(details)
            self.pause(f"Stopped after an unexpected error: {exc}")
            self.show_error_details(details)
        self.root.after(8 if self.desktop is not None else 40, self.poll)

    # Errors
    def report_callback_error(self, kind, value, trace):
        """Tk calls this for an error in a button or timer. Record it rather than lose it."""
        details = "".join(traceback.format_exception(kind, value, trace))
        self.record_error(details)
        self.set_status(f"Something went wrong: {value}")
        self.show_error_details(details)

    def record_error(self, details):
        path = profile_path(0, 0).with_name("errors.log")
        try:
            from importlib.metadata import version

            release = version("mimecontrol")
        except Exception:
            release = "unknown"
        entry = f"--- {datetime.now():%Y-%m-%d %H:%M:%S} · MimeControl {release}\n{details}\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            previous = path.read_text(encoding="utf-8")[-ERROR_LOG_LIMIT:] if path.is_file() else ""
            path.write_text(previous + entry, encoding="utf-8")
        except OSError:
            pass  # The message on screen still has the details.

    def show_error_details(self, details):
        self.error_details = details
        self.copy_error_button.configure(text="Copy error details")
        self.copy_error_button.grid()

    def update_stereo_label(self):
        """The second camera's status, hidden while calibrating so the page never moves."""
        if self.stereo_info.get() and not (self.setup_open or self.page == "calibrate"):
            self.stereo_label.grid()
        else:
            self.stereo_label.grid_remove()

    def learn_control_cameras(self, session):
        """Remember cameras that needed control for a lighter mode, so they open faster next time."""
        for camera in (session, session.aux_camera):
            name = getattr(camera, "camera_name", None)
            if camera is not None and camera.needed_control and name and name not in self.control_cameras:
                self.control_cameras = (self.control_cameras + [name])[-20:]
                self.save_soon()

    def use_sample(self, sample):
        now = monotonic()
        if sample is None or now - sample.timestamp > 0.22:
            if self.last_frame is not None and now - self.last_frame > 0.22:
                self.last_frame = None
                self.gestures.reset(keep_pointer=True)
                if self.desktop is not None:
                    self.desktop.cancel_clicks()
                self.release_pointer()
                if self.pad_enabled:
                    self.pad.highlight(None)
                if self.desktop is None:
                    self.refresh_setup_page()
                if self.capture is not None:
                    self.capture.clear_hands()
                    self.capture.hint = "Waiting for camera frames…"
                    self.draw_step()
                self.clear_preview("Waiting for camera video…")
                self.set_status("Waiting for camera frames…")
                self.frames_lost = True
            return
        if self.last_frame is not None and sample.timestamp - self.last_frame > 0.18:
            self.gestures.reset(keep_pointer=True)
            self.release_pointer()
        self.last_frame = sample.timestamp
        if self.frames_lost:
            # Frames are back. Calibration doesn't set the status on each frame, so restore it here.
            self.frames_lost = False
            if self.setup_open:
                self.set_status(CALIBRATING)
        if sample.preview is not None and not self.setup_open and self.preview_label.winfo_ismapped():
            self.image = tk.PhotoImage(data=sample.preview, format="ppm")
            self.preview_label.configure(image=self.image, text="")
        if self.desktop is None:
            self.set_status(f"{self.camera_title(self.session.camera_index)} is working.")
            if self.start_button.instate(("disabled",)):
                self.refresh_setup_page()
            return
        key = self.session.camera_index, round(100 * sample.width / sample.height)
        if key != self.reach_key:
            # A stream may renegotiate its aspect ratio. Calibration is saved per video shape.
            first = self.reach_key is None or key[0] != self.reach_key[0]
            calibrating = self.setup_open
            self.reach_key = key
            self.saved_reach = load_reach(profile_path(*key))
            if self.saved_reach is not None:
                self.saved_reach.width = self.width_value()
            if first or self.saved_reach is None:
                self.reach_enabled.set(self.saved_reach is not None)
            if calibrating:
                self.begin_calibration()
            else:
                self.apply_reach()
                if first:
                    self.feedback = ("Tracking with your saved calibration." if self.gestures.reach else
                                     "Tracking without calibration. Choose Calibrate to fit your reach.")
                    self.feedback_until = now + 5
        if self.calibrate_on_start:
            self.calibrate_on_start = False
            self.begin_calibration()
        if self.setup_open:
            self.observe_setup(sample)
            return
        if self.session.reach is not self.gestures.reach:
            self.session.reach = self.gestures.reach
        second, second_world = (sample.second, sample.second_world) if self.two_hands.get() else (None, None)
        gesture = self.gestures.update(
            sample.points, sample.width, sample.height, sample.timestamp, self.pad_enabled,
            world=sample.world, second=second, second_world=second_world,
            camera_aim=True, depth_m=sample.depth_m,
        )
        if gesture.release:
            self.finish_hold(now)
        # A fist never aims or clicks, so its swings, pushes and pulls are free for commands.
        if (self.fist_enabled.get() and not self.pad_enabled and gesture.pose not in ("scroll", "zoom")
                and not gesture.held and not self.holding):
            stroke = self.fists.update(sample.points, sample.world, sample.width, sample.timestamp, sample.depth_m)
            if stroke is not None:
                self.release_pointer()
                self.run_gesture(stroke, now)
                return
        if gesture.toggle_pad:
            self.run_gesture("palm_hold", now)
            return
        if gesture.screen_swipe is not None and self.gesture_actions["flick"] == "switch_screen":
            if self.switch_screen(SWIPE_DIRECTIONS[gesture.screen_swipe], now):
                self.gestures.confirm_screen_swipe()
                return
        if gesture.pose == "point" and not self.pad_enabled and self.edge_switch(gesture, now):
            return
        pinch_focus = self.gestures.click_gesture == "Pinch focus"
        waiting_to_aim = ("Number pad open. Leave a small gap between thumb and index to select a key."
                          if self.pad_enabled else "Leave a small gap between thumb and index. Relax the other fingers.")
        idle = "Number pad open. Point to select a key." if self.pad_enabled else "Hand detected. Point to move."
        active = "Number pad active" if self.pad_enabled else "Pointer active"
        if self.gestures.click_gesture == RELAXED_HAND:
            idle = "Extend thumb, index and middle. Let ring and pinky bend loosely."
            active = "Move your knuckles to select a key." if self.pad_enabled else "Aim with your knuckles."
        elif self.gestures.click_gesture == "Palm + pinch":
            idle = "Relax your fingers and move your whole hand."
            active = "Move your palm to select a key." if self.pad_enabled else "Aim with your palm. Pinch to click."
        elif pinch_focus:
            active = "Move your hand to select a key." if self.pad_enabled else "Aim with the midpoint of your thumb and index."
        status = {
            "lost": "Number pad open. Show one hand." if self.pad_enabled else "Tracking. Show one hand to the camera.",
            "idle": waiting_to_aim if pinch_focus else idle,
            "waiting_to_aim": waiting_to_aim,
            "point": active,
            "scroll": "Scrolling",
            "zoom": "Zooming",
            "palm": "Hold your palm to toggle the number pad",
        }[gesture.pose]

        if self.pad_enabled:
            key = pad_key(gesture.pointer)
            self.pad.highlight(key)
            if gesture.click and key is not None:
                self.desktop.type_key(key)
                self.feedback = "Key sent."
                self.feedback_until = now + 0.35
        elif gesture.pose == "point":
            if gesture.press:
                self.start_hold()
            if gesture.held:
                if gesture.drag_motion is not None and self.holding:
                    self.dragged = True
                    self.aim_pointer(self.drag_pointer(gesture.drag_motion, gesture.drag_hand), now)
            elif gesture.camera_pointer is not None and not gesture.aim_held:
                pointer = self.desktop.steer(gesture.camera_pointer)
                targets = self.sticky_targets if self.sticky_enabled.get() else None
                pointer = self.sticky.update(pointer, self.desktop.layout, sample.timestamp, targets,
                                             monitor=self.desktop.active_monitor)
                self.aim_pointer(pointer, now)
            elif not gesture.aim_held:
                self.release_pointer()
            if self.last_pointer is not None and (gesture.click or gesture.right_click):
                self.desktop.click("right" if gesture.right_click else "left", target=self.last_pointer)
                self.feedback = "Right-click sent." if gesture.right_click else "Click sent."
                self.feedback_until = now + 0.35
        else:
            self.release_pointer()
            if gesture.steps:
                if gesture.pose == "scroll":
                    self.desktop.scroll(gesture.steps)
                elif gesture.pose == "zoom":
                    self.desktop.zoom(gesture.steps)
        if gesture.pose not in ("scroll", "zoom") and now < self.feedback_until:
            status = self.feedback
        elif self.edge_message:
            status = self.edge_message
        if self.holding:
            status = ("Dragging. Open your fingers to let go." if gesture.drag_motion is not None else
                      "Holding. Move to drag, or open your fingers to let go.")
        self.set_status(status)


def main():
    configure_dpi()
    root = tk.Tk()
    app = App(root)
    if "--camera-settings" in sys.argv[1:] and sys.platform == "win32":
        # Just the camera's settings, with its live view: a quick stand-in for the maker's app.
        app.settings_only = True
        root.withdraw()
        app.open_camera_settings()
    root.mainloop()
