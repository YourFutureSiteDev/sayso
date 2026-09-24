"""Colours, type and ttk styling for the window.

Tk's defaults are the grey Windows 95 look, so every widget that shows on
screen is restyled here. Kept in one file so the window code is about
behaviour rather than appearance.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Deep, slightly blue-black rather than pure grey: pure grey reads as "unstyled
# Windows", which is exactly the look this is trying to leave behind.
BG = "#07090c"
SIDEBAR = "#090c11"
PANEL = "#171a20"
PANEL_HI = "#1e222a"
LINE = "#272c35"
TEXT = "#eef1f5"
DIM = "#b6bcc6"
MUTED = "#828b98"
ACCENT = "#4ade80"
BLUE = "#60a5fa"
AMBER = "#f5b544"
RED = "#f05252"
VIOLET = "#a78bfa"

RECORDING = RED
BUSY = AMBER

# The tone of a flat glass panel, and the tint given to any widget placed on
# one. The two must stay identical: Tk cannot make a widget translucent, so a
# mismatch shows up as a grey chip floating on the panel.
GLASS = "#1b202a"
GLASS_RGB = (0x1b, 0x20, 0x2a)

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_BOLD = ("Segoe UI Semibold", 10)
FONT_LABEL = ("Segoe UI Semibold", 9)
FONT_TITLE = ("Segoe UI Semibold", 19)
FONT_HERO = ("Segoe UI Light", 30)
FONT_STAT = ("Segoe UI Semibold", 26)
FONT_MONO = ("Cascadia Mono", 9)


def apply(root: tk.Tk) -> ttk.Style:
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure(".", background=BG, foreground=TEXT, font=FONT,
                    fieldbackground=PANEL, bordercolor=LINE, lightcolor=LINE,
                    darkcolor=LINE, focuscolor=ACCENT)
    for name, bg in (("TFrame", BG), ("Panel.TFrame", PANEL),
                     ("Side.TFrame", SIDEBAR), ("Hi.TFrame", PANEL_HI)):
        style.configure(name, background=bg)
    for name, bg, fg, font in (
        ("TLabel", BG, TEXT, FONT),
        ("Panel.TLabel", PANEL, TEXT, FONT),
        ("Side.TLabel", SIDEBAR, TEXT, FONT),
        ("Muted.TLabel", BG, MUTED, FONT_SMALL),
        ("PanelMuted.TLabel", PANEL, MUTED, FONT_SMALL),
        ("SideMuted.TLabel", SIDEBAR, MUTED, FONT_SMALL),
        ("Title.TLabel", BG, TEXT, FONT_TITLE),
        ("Hero.TLabel", BG, TEXT, FONT_HERO),
        ("Heading.TLabel", BG, MUTED, FONT_LABEL),
        ("PanelHeading.TLabel", PANEL, MUTED, FONT_LABEL),
    ):
        style.configure(name, background=bg, foreground=fg, font=font)

    style.configure("TButton", background=PANEL_HI, foreground=TEXT, borderwidth=0,
                    padding=(14, 8), font=FONT)
    style.map("TButton", background=[("active", LINE), ("disabled", PANEL)],
              foreground=[("disabled", MUTED)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#06240f",
                    font=FONT_BOLD, padding=(18, 9), borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#6ee79b"), ("disabled", LINE)],
              foreground=[("disabled", MUTED)])

    style.configure("Stop.TButton", background=RED, foreground="#2a0707",
                    font=FONT_BOLD, padding=(18, 9), borderwidth=0)
    style.map("Stop.TButton", background=[("active", "#f76c6c")])

    style.configure("Quiet.TButton", background=PANEL, foreground=DIM,
                    padding=(12, 7), borderwidth=0, font=FONT_SMALL)
    style.map("Quiet.TButton", background=[("active", PANEL_HI)])

    # Widgets placed on a glass canvas, tinted to the flat panel behind them.
    style.configure("Glass.TLabel", background=GLASS, foreground=TEXT, font=FONT)
    style.configure("Glass.TFrame", background=GLASS)

    for name, bg in (("TCheckbutton", BG), ("Panel.TCheckbutton", PANEL),
                     ("Glass.TCheckbutton", GLASS)):
        style.configure(name, background=bg, foreground=DIM, font=FONT)
        style.map(name, background=[("active", bg)], foreground=[("active", TEXT)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", PANEL_HI)])

    style.configure("TEntry", fieldbackground=PANEL_HI, foreground=TEXT,
                    insertcolor=TEXT, borderwidth=0, padding=7)
    style.configure("TCombobox", fieldbackground=PANEL_HI, background=PANEL_HI,
                    foreground=TEXT, arrowcolor=MUTED, borderwidth=0, padding=7)
    style.map("TCombobox", fieldbackground=[("readonly", PANEL_HI)],
              foreground=[("readonly", TEXT)])
    root.option_add("*TCombobox*Listbox.background", PANEL_HI)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#06240f")

    style.configure("TSeparator", background=LINE)
    style.configure("Vertical.TScrollbar", background=PANEL_HI, troughcolor=BG,
                    borderwidth=0, arrowcolor=MUTED)
    return style


def text_widget(parent: tk.Misc, **kwargs) -> tk.Text:
    """A tk.Text that matches the rest of the window."""
    defaults = dict(bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                    font=FONT_MONO, padx=12, pady=10, borderwidth=0,
                    selectbackground=ACCENT, selectforeground="#06240f",
                    wrap="word", undo=True, highlightthickness=0)
    defaults.update(kwargs)
    return tk.Text(parent, **defaults)


def mix(colour_a: str, colour_b: str, amount: float) -> str:
    """Blend two hex colours, for hover and fade steps."""
    a = [int(colour_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(colour_b[i:i + 2], 16) for i in (1, 3, 5)]
    out = [round(x + (y - x) * amount) for x, y in zip(a, b)]
    return "#%02x%02x%02x" % tuple(out)
