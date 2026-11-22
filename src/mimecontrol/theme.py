"""MimeControl's look: a dark stage lit by an amber spotlight."""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

BG = "#16181d"  # the stage
CARD = "#20232b"  # panels on the stage
CARD_EDGE = "#2e323c"
FIELD = "#2a2e38"  # menus, fields and secondary buttons
FIELD_HOVER = "#363b47"
INK = "#f2efe9"  # main text
MUTED = "#a7a195"  # secondary text
DIM = "#6f6a62"
ACCENT = "#ffb547"  # spotlight amber
ACCENT_HOVER = "#ffc670"
ACCENT_PRESSED = "#e99a2c"
ON_ACCENT = "#1b1406"
SUCCESS = "#6fd39c"
DANGER = "#ff7a6b"
DARK = "#0f1115"  # camera views and the calibration stage
ASSETS = Path(__file__).with_name("assets")
FAMILY = "TkDefaultFont"  # the family in use once apply() has run
FAMILIES = ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "DM Sans",
            "Fira Sans", "Cantarell", "Noto Sans", "DejaVu Sans")


def font(size, *style):
    return (FAMILY, size, *style)


def pick_family(root):
    available = set(tkfont.families(root))
    return next((family for family in FAMILIES if family in available), None)


def rounded(x1, y1, x2, y2, radius):
    """Points for a smoothed polygon with rounded corners."""
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2,
            x1, y2 - r, x1, y1 + r, x1, y1)


def apply(root):
    """Style Tk and ttk for the whole app."""
    global FAMILY
    family = pick_family(root)
    if family:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont",
                     "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                tkfont.nametofont(name, root).configure(family=family)
            except tk.TclError:
                pass
    FAMILY = family or tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    root.configure(bg=BG)
    for pattern, value in (("*TCombobox*Listbox.background", FIELD), ("*TCombobox*Listbox.foreground", INK),
                           ("*TCombobox*Listbox.selectBackground", ACCENT),
                           ("*TCombobox*Listbox.selectForeground", ON_ACCENT),
                           ("*TCombobox*Listbox.borderWidth", 0), ("*Menu.background", FIELD),
                           ("*Menu.foreground", INK), ("*Menu.activeBackground", ACCENT),
                           ("*Menu.activeForeground", ON_ACCENT), ("*Menu.disabledForeground", DIM),
                           ("*Menu.borderWidth", 0), ("*Menu.relief", "flat")):
        root.option_add(pattern, value)

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=INK, fieldbackground=FIELD, bordercolor=CARD_EDGE,
                    lightcolor=CARD_EDGE, darkcolor=CARD_EDGE, troughcolor=FIELD, focuscolor=ACCENT,
                    selectbackground=ACCENT, selectforeground=ON_ACCENT, insertcolor=INK, arrowcolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TButton", background=FIELD, foreground=INK, bordercolor=FIELD, lightcolor=FIELD,
                    darkcolor=FIELD, padding=(14, 7), relief="flat", focusthickness=0)
    style.map("TButton", background=[("disabled", CARD), ("pressed", "#2f333d"), ("active", FIELD_HOVER)],
              foreground=[("disabled", DIM)], bordercolor=[("active", FIELD_HOVER)],
              lightcolor=[("active", FIELD_HOVER)], darkcolor=[("active", FIELD_HOVER)])
    style.configure("Primary.TButton", background=ACCENT, foreground=ON_ACCENT, bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, font=font(10, "bold"))
    style.map("Primary.TButton", background=[("disabled", "#5c5140"), ("pressed", ACCENT_PRESSED),
                                             ("active", ACCENT_HOVER)],
              foreground=[("disabled", "#a39678")], bordercolor=[("active", ACCENT_HOVER)],
              lightcolor=[("active", ACCENT_HOVER)], darkcolor=[("active", ACCENT_HOVER)])
    for name, surface in (("Link.TButton", BG), ("CardLink.TButton", CARD)):
        style.configure(name, padding=(0, 2), background=surface, foreground=ACCENT, borderwidth=0, relief="flat",
                        bordercolor=surface, lightcolor=surface, darkcolor=surface)
        style.map(name, background=[("active", surface), ("pressed", surface)],
                  foreground=[("active", ACCENT_HOVER), ("disabled", DIM)])
    style.configure("TMenubutton", background=FIELD, foreground=INK, bordercolor=FIELD, lightcolor=FIELD,
                    darkcolor=FIELD, padding=(14, 7), arrowcolor=ACCENT, relief="flat")
    style.map("TMenubutton", background=[("active", FIELD_HOVER), ("pressed", "#2f333d")])
    for name, surface in (("TCheckbutton", BG), ("TRadiobutton", BG), ("Card.TCheckbutton", CARD),
                          ("Card.TRadiobutton", CARD)):
        style.configure(name, background=surface, foreground=INK, indicatorbackground=FIELD,
                        indicatorforeground=ON_ACCENT, upperbordercolor=CARD_EDGE, lowerbordercolor=CARD_EDGE,
                        focuscolor=surface)
        style.map(name, background=[("active", surface)], foreground=[("disabled", DIM)],
                  indicatorbackground=[("selected", ACCENT), ("active", FIELD_HOVER)])
    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=INK, arrowcolor=ACCENT,
                    bordercolor=CARD_EDGE, lightcolor=FIELD, darkcolor=FIELD, padding=(8, 4), arrowsize=14)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], selectbackground=[("readonly", FIELD)],
              selectforeground=[("readonly", INK)], foreground=[("readonly", INK), ("disabled", DIM)],
              background=[("active", FIELD_HOVER)], bordercolor=[("focus", ACCENT)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=INK, bordercolor=CARD_EDGE, lightcolor=FIELD,
                    darkcolor=FIELD, insertcolor=INK, padding=(8, 4))
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("Horizontal.TScale", background=ACCENT, troughcolor=FIELD, bordercolor=CARD_EDGE,
                    lightcolor=ACCENT, darkcolor=ACCENT_PRESSED)
    style.map("Horizontal.TScale", background=[("active", ACCENT_HOVER)])
    return family


def dark_title_bar(window):
    """Ask Windows 10 and 11 for a dark title bar to match. Elsewhere this does nothing."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        handle = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, then its pre-2004 number
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
    except (AttributeError, OSError, tk.TclError):
        pass


def scaled_image(root, stem, sizes):
    """The asset closest to the size the screen's scaling calls for, as a Tk image."""
    try:
        wanted = sizes[0] * float(root.winfo_fpixels("1i")) / 96
    except (TypeError, ValueError, tk.TclError):
        wanted = sizes[0]
    size = min(sizes, key=lambda candidate: abs(candidate - wanted))
    try:
        return tk.PhotoImage(master=root, file=str(ASSETS / f"{stem}-{size}.png"))
    except (tk.TclError, RuntimeError):
        return None


def set_icon(window):
    images = []
    for size in (16, 32, 48, 64, 128, 256):
        try:
            images.append(tk.PhotoImage(master=window, file=str(ASSETS / f"icon-{size}.png")))
        except tk.TclError:
            pass
    if images:
        window.iconphoto(True, *reversed(images))
        window._mimecontrol_icons = images  # Tk forgets images nothing refers to.


class Card:
    """A rounded panel that grows with what it holds. Put widgets in .body; place it like any widget."""

    def __init__(self, parent, *, padding=(16, 14), radius=14, fill=CARD, edge=CARD_EDGE, surface=BG, width=480):
        self.canvas = canvas = tk.Canvas(parent, bg=surface, highlightthickness=0, bd=0, width=width, height=1)
        self.padding, self.radius, self.fill, self.edge = padding, radius, fill, edge
        self.body = tk.Frame(canvas, bg=fill)
        self.window = canvas.create_window(padding[0], padding[1], window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _: canvas.after_idle(self.redraw))
        canvas.bind("<Configure>", lambda _: self.redraw())

    def __getattr__(self, name):
        # pack, grid, pack_forget, winfo_ismapped and the rest come from the canvas.
        return getattr(self.canvas, name)

    def redraw(self):
        canvas = self.canvas
        try:
            width = canvas.winfo_width()
            if width <= 1:
                width = int(canvas.cget("width"))
            pad_x, pad_y = self.padding
            canvas.itemconfigure(self.window, width=max(1, width - 2 * pad_x))
            height = self.body.winfo_reqheight() + 2 * pad_y
            if int(canvas.cget("height")) != height:
                canvas.configure(height=height)
            canvas.delete("panel")
            canvas.create_polygon(rounded(0, 0, width - 1, height - 1, self.radius), smooth=True, fill=self.fill,
                                  outline=self.edge, tags="panel")
            canvas.tag_lower("panel")
        except (tk.TclError, TypeError, ValueError):
            pass
