"""The Sayso window: a dashboard, not a settings dialog.

Left rail to move between Home, History, Dictionary and Settings. Home carries
the live state and the numbers; the rest is where things get changed.

Everything that moves is animated on Tk's own `after` loop: the rail indicator
slides, the stat numbers count up slowly, the level meter follows the
microphone. There is no animation library here and there does not need to be.

Tk is not thread safe. The engine's status callbacks arrive on worker threads,
go onto a queue, and the UI thread drains it in `_pump`. No engine thread ever
touches a widget.
"""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import messagebox, ttk

from . import glass, single, theme
from .audio import list_devices
from .config import CONFIG_PATH, DATA_DIR, Config
from .history import History

log = logging.getLogger(__name__)

MODELS = [
    ("large-v3", "most accurate, 3.1 GB of VRAM"),
    ("large-v3-turbo", "4x faster, about a point less accurate"),
    ("medium", "half the VRAM, noticeably worse on names"),
    ("small", "last resort if the GPU is busy"),
]

PAGES = [
    ("home", "Home"),
    ("history", "History"),
    ("dictionary", "Dictionary"),
    ("settings", "Settings"),
]

RAIL_W = 196
ROW_H = 40
ROW_TOP = 118

DOT = {"idle": theme.MUTED, "ready": theme.ACCENT, "recording": theme.RED,
       "busy": theme.AMBER, "error": theme.VIOLET}


class SaysoWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Sayso")
        self.geometry("880x660")
        self.minsize(820, 620)
        theme.apply(self)

        self.cfg = Config.load()
        self.cfg.write_template()
        self.engine = None
        self.tray = None
        self.bar = None
        self._events: queue.Queue = queue.Queue()
        self._devices = list_devices()
        self._dirty = False
        self._page = "home"
        self._indicator_y = float(ROW_TOP)
        self._indicator_target = float(ROW_TOP)
        self._hover_row: int | None = None
        self._counts: dict[str, float] = {}
        self._targets: dict[str, float] = {}
        self._level = 0.0
        self._glass_phase = 0.0
        self._glass_dirty = True
        self._glass_at = 0.0
        self._panel_boxes: list = []
        # Frosted panel rectangles per page, in that page's canvas coordinates.
        self._page_boxes: dict[str, list] = {}
        self._hero_text = "Getting ready"
        self._hero_sub_text = "loading the model"

        self._build()
        self._pump()
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self._start_engine)

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        self.rail = tk.Canvas(body, width=RAIL_W, bg=theme.SIDEBAR,
                              highlightthickness=0, bd=0)
        self.rail.pack(side="left", fill="y")
        self._build_rail()

        right = ttk.Frame(body, padding=(26, 22, 26, 0))
        right.pack(side="left", fill="both", expand=True)
        self.content = ttk.Frame(right)
        self.content.pack(fill="both", expand=True)

        self.pages: dict[str, ttk.Frame] = {}
        self._build_home()
        self._build_history()
        self._build_dictionary()
        self._build_settings()
        self._show_page("home")
        self._build_footer(right)

    # ------------------------------------------------------------------ rail

    def _build_rail(self) -> None:
        c = self.rail
        # Created first so everything else draws over it.
        self._rail_bg = c.create_image(0, 0, anchor="nw")
        c.create_text(26, 38, text="Sayso", anchor="w", fill=theme.TEXT,
                      font=("Segoe UI Semibold", 18))
        c.create_text(26, 62, text="speak, it types", anchor="w",
                      fill=theme.MUTED, font=theme.FONT_TINY)

        self._indicator = c.create_rectangle(
            0, ROW_TOP, 3, ROW_TOP + ROW_H, fill=theme.ACCENT, outline="")
        self._rail_items: dict[str, dict] = {}
        for i, (key, label) in enumerate(PAGES):
            y = ROW_TOP + i * ROW_H
            hit = c.create_rectangle(0, y, RAIL_W, y + ROW_H, fill="", outline="",
                                     tags=(f"row{i}",))
            text = c.create_text(26, y + ROW_H / 2, text=label, anchor="w",
                                 fill=theme.MUTED, font=theme.FONT, tags=(f"row{i}",))
            self._rail_items[key] = {"y": y, "text": text, "hit": hit, "index": i}
            c.tag_bind(f"row{i}", "<Button-1>",
                       lambda _e, k=key: self._show_page(k))
            c.tag_bind(f"row{i}", "<Enter>",
                       lambda _e, k=key: self._hover_rail(k, True))
            c.tag_bind(f"row{i}", "<Leave>",
                       lambda _e, k=key: self._hover_rail(k, False))

        # Status, pinned to the bottom of the rail.
        self._rail_dot = c.create_oval(26, 0, 36, 0, fill=theme.MUTED, outline="")
        self._rail_status = c.create_text(44, 0, text="starting", anchor="w",
                                          fill=theme.DIM, font=theme.FONT_SMALL)
        self._rail_hint = c.create_text(26, 0, text="", anchor="w",
                                        fill=theme.MUTED, font=theme.FONT_TINY)
        c.bind("<Configure>", lambda _e: self._place_rail_status())

    def _place_rail_status(self) -> None:
        h = self.rail.winfo_height()
        self.rail.coords(self._rail_dot, 26, h - 62, 35, h - 53)
        self.rail.coords(self._rail_status, 44, h - 57)
        self.rail.coords(self._rail_hint, 26, h - 34)

    def _hover_rail(self, key: str, entering: bool) -> None:
        if key == self._page:
            return
        self.rail.itemconfigure(self._rail_items[key]["text"],
                                fill=theme.DIM if entering else theme.MUTED)

    def _show_page(self, key: str) -> None:
        self._page = key
        for name, frame in self.pages.items():
            frame.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, item in self._rail_items.items():
            self.rail.itemconfigure(item["text"],
                                    fill=theme.TEXT if name == key else theme.MUTED,
                                    font=theme.FONT_BOLD if name == key else theme.FONT)
        self._indicator_target = float(self._rail_items[key]["y"])
        # The aurora is cropped per page, so it has to be recomposited on a
        # switch rather than waiting for the next drift tick.
        self._glass_dirty = True
        if key == "history":
            self._load_history()
        if key == "settings" and not getattr(self, "_settings_drawn", False):
            self.after(30, self._draw_settings)

    # ------------------------------------------------------------------ home

    def _build_home(self) -> None:
        """One canvas for the whole page.

        The hero text is a canvas item rather than a label because a ttk widget
        paints a solid rectangle, and a solid rectangle over a moving aurora
        looks like a hole in it. On the canvas the text sits directly on the
        glass and Tk still renders the font properly.
        """
        page = ttk.Frame(self.content)
        self.pages["home"] = page

        self.home_canvas = tk.Canvas(page, bg=glass.BASE, highlightthickness=0, bd=0)
        self.home_canvas.pack(fill="both", expand=True)
        self.home_canvas.bind("<Configure>", lambda _e: self._draw_home())
        self._home_drawn = False
        self._hero_item = None
        self._hero_sub_item = None

    def _draw_home(self) -> None:
        """Lay the tiles out for the current width.

        Only the text and the meter bars are canvas items. The panels
        themselves are part of the glass image behind them, so they can be
        genuinely translucent, which no Tk widget can be.
        """
        c = self.home_canvas
        c.delete("all")
        width = max(c.winfo_width(), 420)
        gap = 14
        tile_w = (width - gap) // 2
        tile_h = 96
        top = 118                     # room for the hero lines above the tiles

        self._home_bg = c.create_image(0, 0, anchor="nw")
        self._hero_item = c.create_text(2, 34, text=self._hero_text, anchor="w",
                                        fill=theme.TEXT, font=theme.FONT_HERO)
        self._hero_sub_item = c.create_text(4, 68, text=self._hero_sub_text,
                                            anchor="w", fill=theme.DIM,
                                            font=theme.FONT_SMALL)
        self._panel_boxes = []
        self._tiles = {}
        specs = [
            ("words", "Words dictated", theme.ACCENT, 0, 0),
            ("saved", "Minutes saved", theme.BLUE, 1, 0),
            ("wpm", "Words per minute", theme.VIOLET, 0, 1),
            ("count", "Dictations", theme.AMBER, 1, 1),
        ]
        for key, label, colour, col, row in specs:
            x = col * (tile_w + gap)
            y = top + row * (tile_h + gap)
            self._panel_boxes.append((x, y, x + tile_w, y + tile_h, 16, 11))
            c.create_rectangle(x + 16, y + 22, x + 19, y + tile_h - 22,
                               fill=colour, outline="")
            self._tiles[key] = c.create_text(x + 32, y + 40, text="0", anchor="w",
                                             fill=theme.TEXT, font=theme.FONT_STAT)
            c.create_text(x + 32, y + 70, text=label, anchor="w",
                          fill=theme.DIM, font=theme.FONT_SMALL)

        meter_y = top + 2 * (tile_h + gap) + 8
        self._panel_boxes.append((0, meter_y, width, meter_y + 84, 16, 11))
        c.create_text(24, meter_y + 24, text="MICROPHONE", anchor="w",
                      fill=theme.DIM, font=theme.FONT_LABEL)
        self._meter_bars = []
        bars = 42
        span = width - 48
        bw = max(2, (span - (bars - 1) * 4) // bars)
        for i in range(bars):
            x = 24 + i * (bw + 4)
            self._meter_bars.append(
                c.create_line(x, meter_y + 58, x, meter_y + 62,
                              fill=theme.LINE, width=bw, capstyle="round"))
        self._page_boxes["home"] = self._panel_boxes
        self._home_drawn = True
        self._glass_dirty = True
        self._refresh_stats()

    # ------------------------------------------------------------------ glass

    def _render_glass(self) -> None:
        """One aurora across the whole window, cropped to each canvas.

        Rendered as a single image so the colour runs continuously from the
        rail into the content rather than stopping at the seam, then frosted
        panels are composited where the tiles sit.

        Redrawn a few times a second, not every frame: a full frame costs
        about 45 ms, and the drift is slow enough that nobody can tell.
        """
        from PIL import ImageTk

        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        image = glass.aurora(w, h, self._glass_phase, base=glass.BASE,
                             blobs=glass.BLOBS)
        glass.frost(image, (0, 0, RAIL_W, h), radius=0, alpha=7, border=20,
                    lift=10)

        canvas = {"home": getattr(self, "home_canvas", None),
                  "settings": getattr(self, "settings_canvas", None)}.get(self._page)

        # Panel boxes are in the page canvas's own coordinates, so they have to
        # be shifted by where that canvas sits in the window before they are
        # composited into the single shared aurora.
        if canvas is not None:
            ox, oy = self._canvas_origin(canvas)
            # A scrolled canvas moves its items but not the window, so the
            # panels have to be frosted where they currently appear, not where
            # they were placed.
            top = self._scroll_top(canvas)
            for x0, y0, x1, y1, radius, alpha in self._page_boxes.get(self._page, []):
                glass.frost(image, (ox + x0, oy + y0 - top,
                                    ox + x1, oy + y1 - top),
                            radius=radius, alpha=alpha or 11,
                            # alpha 0 means a flat panel: pages with opaque
                            # widgets on them need something the widgets can
                            # match exactly.
                            solid=theme.GLASS_RGB if alpha == 0 else None)

        self._rail_img = ImageTk.PhotoImage(image.crop((0, 0, RAIL_W, h)))
        self.rail.itemconfigure(self._rail_bg, image=self._rail_img)

        item = {"home": getattr(self, "_home_bg", None),
                "settings": getattr(self, "_settings_bg", None)}.get(self._page)
        if canvas is not None and item is not None:
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if cw > 1 and ch > 1:
                ox, oy = self._canvas_origin(canvas)
                self._page_img = ImageTk.PhotoImage(
                    image.crop((ox, oy, ox + cw, oy + ch)))
                canvas.itemconfigure(item, image=self._page_img)
                # Pinned to the top of the viewport, not the top of the
                # content, so the backdrop stays put while the page scrolls.
                canvas.coords(item, 0, self._scroll_top(canvas))
                canvas.tag_lower(item)

    @staticmethod
    def _scroll_top(canvas: tk.Canvas) -> int:
        """How far the canvas is scrolled, in canvas coordinates."""
        try:
            return int(canvas.canvasy(0))
        except tk.TclError:
            return 0

    def _canvas_origin(self, canvas: tk.Canvas) -> tuple[int, int]:
        """Where a page canvas sits inside the window."""
        try:
            return (canvas.winfo_rootx() - self.winfo_rootx(),
                    canvas.winfo_rooty() - self.winfo_rooty())
        except tk.TclError:
            return (RAIL_W + 26, 22)

    def _refresh_stats(self) -> None:
        """Set the count-up targets from the store, without jumping."""
        store = self.engine.history if self.engine is not None else None
        if store is None:
            return
        self._targets = {
            "words": float(store.total_words),
            "saved": float(round(store.minutes_saved)),
            "wpm": float(round(store.words_per_minute)),
            "count": float(len(store.entries)),
        }

    # --------------------------------------------------------------- history

    def _build_history(self) -> None:
        page = ttk.Frame(self.content)
        self.pages["history"] = page

        ttk.Label(page, text="History", style="Title.TLabel").pack(anchor="w")
        self.totals = ttk.Label(page, text="", style="Muted.TLabel")
        self.totals.pack(anchor="w", pady=(4, 0))
        ttk.Label(page,
                  text="Kept on this machine only. There is nowhere for it to go: "
                       "the whole app runs offline.",
                  style="Muted.TLabel", wraplength=560, justify="left").pack(
            anchor="w", pady=(2, 0))

        self.activity = theme.text_widget(page, height=17, state="disabled")
        self.activity.pack(fill="both", expand=True, pady=(16, 10))
        self.activity.tag_configure("time", foreground=theme.MUTED)

        row = ttk.Frame(page)
        row.pack(fill="x")
        ttk.Button(row, text="Copy the last one", style="Quiet.TButton",
                   command=self._copy_last).pack(side="left")
        ttk.Button(row, text="Clear history", style="Quiet.TButton",
                   command=self._clear_history).pack(side="left", padx=8)

    # ------------------------------------------------------------ dictionary

    def _build_dictionary(self) -> None:
        page = ttk.Frame(self.content)
        self.pages["dictionary"] = page

        ttk.Label(page, text="Dictionary", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page,
                  text="Names, clients, streets, anything unusual you say often. "
                       "This is the biggest accuracy win there is: it steers the "
                       "model before it decides, rather than patching the text after.",
                  style="Muted.TLabel", wraplength=560, justify="left").pack(
            anchor="w", pady=(4, 0))

        ttk.Label(page, text="YOUR WORDS", style="Heading.TLabel").pack(
            anchor="w", pady=(18, 6))
        self.vocab_text = theme.text_widget(page, height=9)
        self.vocab_text.pack(fill="both", expand=True)
        self.vocab_text.bind("<<Modified>>", self._on_text_edit)

        ttk.Label(page, text="CORRECTIONS", style="Heading.TLabel").pack(
            anchor="w", pady=(16, 2))
        ttk.Label(page, text="One per line as  what it hears = what you meant.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.fix_text = theme.text_widget(page, height=6)
        self.fix_text.pack(fill="both", expand=True)
        self.fix_text.bind("<<Modified>>", self._on_text_edit)

        self._load_word_boxes()

    # -------------------------------------------------------------- settings

    def _build_settings(self) -> None:
        """Settings on the glass.

        Headings are canvas text and the controls are placed on the canvas with
        `create_window`, because a Frame filling the page would cover the
        aurora and there is no way to make a Tk widget translucent. Only the
        controls themselves are opaque, and they sit inside the frosted panels
        drawn behind them.
        """
        page = ttk.Frame(self.content)
        self.pages["settings"] = page
        c = tk.Canvas(page, bg=glass.BASE, highlightthickness=0, bd=0)
        c.pack(fill="both", expand=True)
        self.settings_canvas = c

        self.device_var = tk.StringVar(value=self._device_label(self.cfg.input_device))
        self.model_var = tk.StringVar(value=self._model_label(self.cfg.model))
        self.bar_var = tk.BooleanVar(value=self.cfg.show_bar)
        self.autostart_var = tk.BooleanVar(value=single.autostart_enabled())
        self.beep_var = tk.BooleanVar(value=self.cfg.sound_feedback)
        self.space_var = tk.BooleanVar(value=self.cfg.trailing_space)
        self.fix_var = tk.BooleanVar(value=self.cfg.apply_corrections)
        self.vad_var = tk.BooleanVar(value=self.cfg.vad)
        self.device_var.trace_add("write", self._mark_dirty)
        self.model_var.trace_add("write", self._mark_dirty)

        # Not shown: rarely touched, but _collect still reads them.
        self.idle_var = tk.StringVar(value=str(int(self.cfg.idle_unload_seconds // 60)))
        self.beam_var = tk.StringVar(value=str(self.cfg.beam_size))

        c.bind("<Configure>", lambda _e: self._draw_settings())
        self._settings_drawn = False

    def _draw_settings(self) -> None:
        c = self.settings_canvas
        c.delete("all")
        width = max(c.winfo_width(), 460)
        self._settings_bg = c.create_image(0, 0, anchor="nw")
        boxes: list = []
        y = 6

        c.create_text(2, y + 16, text="Settings", anchor="w", fill=theme.TEXT,
                      font=theme.FONT_TITLE)
        y += 52

        def heading(title: str, blurb: str = "") -> None:
            nonlocal y
            c.create_text(4, y, text=title.upper(), anchor="w", fill=theme.MUTED,
                          font=theme.FONT_LABEL)
            y += 18
            if blurb:
                c.create_text(4, y, text=blurb, anchor="nw", fill=theme.DIM,
                              font=theme.FONT_SMALL, width=width - 20)
                y += 16 * (1 + len(blurb) // 74)
            y += 8

        def panel(height: int) -> int:
            """A frosted box, milkier than the ones on Home.

            The controls placed inside it are opaque and tinted to one fixed
            colour, so the panel has to be close to flat for them to blend. A
            lightly frosted panel lets the aurora through and every checkbox
            then reads as a grey chip sitting on top of it.
            """
            nonlocal y
            boxes.append((0, y, width, y + height, 14, 0))
            top = y
            y += height + 22
            return top

        heading("Dictation key", "Press once to start and once to stop. Holding it "
                                 "works too and ends when you let go.")
        top = panel(52)
        self.hotkey_label = ttk.Label(c, text=self._pretty_key(self.cfg.hotkey),
                                      style="Glass.TLabel", font=theme.FONT_BOLD)
        c.create_window(18, top + 26, window=self.hotkey_label, anchor="w")
        c.create_window(width - 18, top + 26, anchor="e", window=ttk.Button(
            c, text="Rebind", style="Quiet.TButton", command=self._rebind))

        heading("Microphone", "The wrong one costs more accuracy than any other "
                              "setting here.")
        top = panel(56)
        c.create_window(18, top + 28, anchor="w", width=width - 36, window=ttk.Combobox(
            c, textvariable=self.device_var, state="readonly",
            values=["System default"] + [f"[{i}] {n}" for i, n, _c in self._devices]))

        heading("Model")
        top = panel(56)
        c.create_window(18, top + 28, anchor="w", width=width - 36, window=ttk.Combobox(
            c, textvariable=self.model_var, state="readonly",
            values=[f"{n} - {note}" for n, note in MODELS]))

        heading("Behaviour")
        rows = (
            (self.bar_var, "Show the bar while dictating", self._toggle_bar),
            (self.autostart_var, "Start Sayso when Windows starts",
             self._toggle_autostart),
            (self.beep_var, "Beep when recording starts and stops", self._mark_dirty),
            (self.space_var, "Add a space after each dictation", self._mark_dirty),
            (self.fix_var, "Fix the spelling of names (never changes your words)",
             self._mark_dirty),
            (self.vad_var, "Trim silence before transcribing", self._mark_dirty),
        )
        top = panel(26 * len(rows) + 22)
        for i, (var, label, command) in enumerate(rows):
            c.create_window(18, top + 22 + i * 26, anchor="w", window=ttk.Checkbutton(
                c, text=label, variable=var, style="Glass.TCheckbutton",
                command=command))

        heading("Checks")
        top = panel(56)
        c.create_window(18, top + 28, anchor="w", window=ttk.Button(
            c, text="Run the self test", style="Quiet.TButton",
            command=self._run_selftest))
        c.create_window(170, top + 28, anchor="w", window=ttk.Button(
            c, text="Settings folder", style="Quiet.TButton",
            command=lambda: webbrowser.open(str(DATA_DIR))))

        self._page_boxes["settings"] = boxes
        self._settings_drawn = True
        self._glass_dirty = True

        # The page is taller than the window, so it scrolls. Without this the
        # last panel is simply cut off with no way to reach it.
        c.configure(scrollregion=(0, 0, width, y + 10))
        c.bind("<MouseWheel>", self._scroll_settings)
        c.bind("<Enter>", lambda _e: c.focus_set())

    def _scroll_settings(self, event) -> None:  # noqa: ANN001
        self.settings_canvas.yview_scroll(-1 * (event.delta // 120), "units")
        self._glass_dirty = True

    def _section(self, parent: tk.Misc, title: str, blurb: str = "") -> ttk.Frame:
        ttk.Label(parent, text=title.upper(), style="Heading.TLabel").pack(
            anchor="w", pady=(18, 6))
        if blurb:
            ttk.Label(parent, text=blurb, style="Muted.TLabel",
                      wraplength=560, justify="left").pack(anchor="w", pady=(0, 8))
        box = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        box.pack(fill="x")
        return box

    # ---------------------------------------------------------------- footer

    def _build_footer(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, padding=(0, 14, 0, 14))
        bar.pack(fill="x")
        self.saved = ttk.Label(bar, text="", style="Muted.TLabel")
        self.saved.pack(side="left")
        self.save_btn = ttk.Button(bar, text="Save changes", style="Accent.TButton",
                                   command=self._save, state="disabled")
        self.save_btn.pack(side="right")
        self.power = ttk.Button(bar, text="Stop listening", style="Quiet.TButton",
                                command=self._toggle_engine)
        self.power.pack(side="right", padx=8)
        ttk.Button(bar, text="Hide to tray", style="Quiet.TButton",
                   command=self._hide).pack(side="right")

    # ------------------------------------------------------------- animation

    def _tick(self) -> None:
        """One loop for every moving thing in the window."""
        # Rail indicator slides to the selected row.
        if abs(self._indicator_y - self._indicator_target) > 0.4:
            self._indicator_y += (self._indicator_target - self._indicator_y) * 0.28
            self.rail.coords(self._indicator, 0, self._indicator_y,
                             3, self._indicator_y + ROW_H)

        if self._home_drawn:
            self._tick_counts()
            self._tick_meter()

        # The glass costs about 45 ms a frame, so it drifts on its own slower
        # clock while the numbers and the meter keep running at 30.
        now = time.monotonic()
        if self._glass_dirty or now - self._glass_at > 0.14:
            self._glass_at = now
            self._glass_dirty = False
            self._glass_phase += 0.05
            try:
                self._render_glass()
            except Exception:
                log.exception("glass render failed")

        self.after(33, self._tick)

    def _tick_counts(self) -> None:
        """Count up slowly. A number that snaps into place reads as a glitch."""
        for key, target in self._targets.items():
            current = self._counts.get(key, 0.0)
            if abs(current - target) < 0.5:
                if current != target:
                    self._counts[key] = target
                    self._set_tile(key, target)
                continue
            # ~2.5s to settle, which is the pace he asked for.
            self._counts[key] = current + (target - current) * 0.035
            self._set_tile(key, self._counts[key])

    def _set_tile(self, key: str, value: float) -> None:
        text = f"{int(round(value)):,}"
        self.home_canvas.itemconfigure(self._tiles[key], text=text)

    def _tick_meter(self) -> None:
        live = self.engine.level if (self.engine and self.engine.active) else 0.0
        self._level += (live * 3.4 - self._level) * (0.45 if live else 0.12)
        self._level = max(0.0, min(1.0, self._level))
        count = len(self._meter_bars)
        for i, item in enumerate(self._meter_bars):
            coords = self.home_canvas.coords(item)
            if not coords:
                continue
            x = coords[0]
            mid = (coords[1] + coords[3]) / 2
            # A soft arch so the meter reads as a voice, not a bar chart.
            shape = (1 - abs(i - (count - 1) / 2) / ((count - 1) / 2)) ** 0.7
            half = max(2.0, self._level * shape * 22)
            self.home_canvas.coords(item, x, mid - half, x, mid + half)
            self.home_canvas.itemconfigure(
                item, fill=theme.mix(theme.LINE, theme.ACCENT,
                                     min(1.0, self._level * shape * 1.4)))

    # ---------------------------------------------------------------- engine

    def _start_engine(self) -> None:
        from .app import Sayso

        self.cfg = Config.load()
        self.engine = Sayso(self.cfg, on_status=self._status_from_thread)
        self.engine.start()
        self.power.configure(text="Stop listening")
        self._ensure_bar()
        self._load_history()
        self._refresh_stats()
        threading.Thread(target=self.engine.preload, daemon=True).start()

    def _stop_engine(self) -> None:
        if self.engine is not None:
            self.engine.close()
            self.engine = None
        self._destroy_bar()
        self.power.configure(text="Start listening")
        self._set_status("off", "idle")

    def _toggle_engine(self) -> None:
        self._stop_engine() if self.engine is not None else self._start_engine()

    def _restart_engine(self) -> None:
        if self.engine is not None:
            self._stop_engine()
            self._start_engine()

    # ------------------------------------------------------------ floating bar

    def _ensure_bar(self) -> None:
        from .widget import Bar

        if self.bar is not None or not self.cfg.show_bar:
            return
        self.bar = Bar(self, on_toggle=self._bar_toggle, on_confirm=self._bar_confirm,
                       on_cancel=self._bar_cancel,
                       level_source=lambda: self.engine.level if self.engine else 0.0)

    def _destroy_bar(self) -> None:
        if self.bar is not None:
            self.bar.destroy()
            self.bar = None

    def _bar_toggle(self) -> None:
        if self.engine is not None:
            self.engine.toggle()

    def _bar_confirm(self) -> None:
        if self.engine is not None and self.engine.active:
            self.engine.finish()

    def _bar_cancel(self) -> None:
        if self.engine is not None:
            self.engine.cancel()

    # ---------------------------------------------------------------- status

    def _status_from_thread(self, text: str) -> None:
        """Runs on worker threads, so it only touches the queue."""
        self._events.put(text)

    def _pump(self) -> None:
        while True:
            try:
                text = self._events.get_nowait()
            except queue.Empty:
                break
            self._apply_status(text)
        if single.take_wake_request():
            self._show()
        self.after(80, self._pump)

    def _apply_status(self, text: str) -> None:
        kind = "ready"
        if text.startswith("recording"):
            kind = "recording"
        elif text.startswith(("transcribing", "loading")):
            kind = "busy"
        elif text.startswith("error"):
            kind = "error"
        self._set_status(text, kind)
        self._set_hero(text)
        if text.startswith("typed") and self.engine is not None and self.engine.last:
            self._log_dictation(self.engine.last)
            self._refresh_stats()
        if self.tray is not None:
            self.tray.set_status(text)
        self._update_bar(text)

    def _set_hero(self, text: str) -> None:
        key = self._pretty_key(self.cfg.hotkey)
        lines = {
            "recording": ("Listening", f"press {key} again, or click the tick"),
            "transcribing": ("Working it out", "transcribing what you said"),
            "loading": ("Getting ready", "loading the model onto the graphics card"),
            "typed": ("Typed it", text),
            "nothing heard": ("Nothing to type", text),
            "too short": ("Nothing to type", text),
            "cancelled": ("Thrown away", text),
            "ready": ("Ready", f"press {key} anywhere and talk"),
            "off": ("Not listening", "press Start listening to switch it back on"),
        }
        for prefix, (head, sub) in lines.items():
            if text.startswith(prefix):
                self._hero(head, sub)
                return

    def _hero(self, head: str, sub: str) -> None:
        """Canvas text, so it sits on the glass rather than on a grey patch."""
        self._hero_text, self._hero_sub_text = head, sub
        if self._hero_item is not None:
            self.home_canvas.itemconfigure(self._hero_item, text=head)
            self.home_canvas.itemconfigure(self._hero_sub_item, text=sub)

    def _set_status(self, text: str, kind: str) -> None:
        self.rail.itemconfigure(self._rail_status, text=text)
        self.rail.itemconfigure(self._rail_dot, fill=DOT.get(kind, theme.MUTED))
        self.rail.itemconfigure(self._rail_hint,
                                text=self._pretty_key(self.cfg.hotkey).lower()
                                + " to dictate")

    def _update_bar(self, text: str) -> None:
        """Only on screen while a dictation is happening.

        Keyed on the message, not the colour: loading the model is also "busy",
        and it is not a dictation, so it must not put the bar on his screen.
        """
        if self.bar is None:
            return
        if text.startswith("recording"):
            self.bar.set_state("recording")
            self.bar.show()
        elif text.startswith("transcribing"):
            self.bar.set_state("busy")
            self.bar.show()
        else:
            self.bar.set_state("idle")
            if self.bar.visible:
                self.after(350, self.bar.hide)

    # --------------------------------------------------------------- history

    def _load_history(self) -> None:
        store = self.engine.history if self.engine is not None else History(
            keep_days=self.cfg.keep_history_days)
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        for entry in store.entries[-200:]:
            self.activity.insert("end", entry.stamp.strftime("%d %b %H:%M  "), "time")
            self.activity.insert("end", entry.text.strip() + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")
        self._update_totals(store)

    def _update_totals(self, store: History) -> None:
        if not store.entries:
            self.totals.configure(text="Nothing dictated yet")
            return
        self.totals.configure(
            text=f"{store.total_words:,} words · {store.total_seconds / 60:.0f} min "
                 f"spoken · {store.words_per_minute:.0f} wpm · "
                 f"{store.minutes_saved:.0f} min saved over typing")

    def _log_dictation(self, result) -> None:  # noqa: ANN001
        self.activity.configure(state="normal")
        self.activity.insert("end", datetime.now().strftime("%d %b %H:%M  "), "time")
        self.activity.insert("end", result.text.strip() + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")
        if self.engine is not None:
            self._update_totals(self.engine.history)

    def _clear_history(self) -> None:
        if self.engine is None:
            return
        if messagebox.askokcancel("Clear history",
                                  "Delete every dictation kept on this machine?"):
            self.engine.history.clear()
            self._load_history()
            self._refresh_stats()
            self._counts = {}

    def _copy_last(self) -> None:
        if self.engine is not None and self.engine.last:
            text = self.engine.last.text
            self.after(0, lambda: (self.clipboard_clear(), self.clipboard_append(text)))

    # ------------------------------------------------------------- behaviour

    @staticmethod
    def _pretty_key(name: str) -> str:
        return (name.replace("_l", " (left)").replace("_r", " (right)")
                .replace("_gr", "Gr").replace("_", " ").title())

    def _device_label(self, index: int | None) -> str:
        if index is None:
            return "System default"
        for i, name, _c in self._devices:
            if i == index:
                return f"[{i}] {name}"
        return "System default"

    @staticmethod
    def _model_label(model: str) -> str:
        for name, note in MODELS:
            if name == model:
                return f"{name} - {note}"
        return model

    def _mark_dirty(self, *_args) -> None:
        self._dirty = True
        self.save_btn.configure(state="normal")
        self.saved.configure(text="unsaved changes")

    def _on_text_edit(self, event) -> None:  # noqa: ANN001
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._mark_dirty()

    def _load_word_boxes(self) -> None:
        extra_vocab: list[str] = []
        extra_fixes: dict[str, str] = {}
        if CONFIG_PATH.is_file():
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            extra_vocab = saved.get("vocabulary") or []
            extra_fixes = saved.get("corrections") or {}
        self.vocab_text.insert("1.0", "\n".join(extra_vocab))
        self.fix_text.insert("1.0", "\n".join(f"{k} = {v}" for k, v in extra_fixes.items()))
        self.vocab_text.edit_modified(False)
        self.fix_text.edit_modified(False)

    def _toggle_autostart(self) -> None:
        """Applies straight away; the registry is the truth, not the tick box."""
        actual = single.set_autostart(self.autostart_var.get())
        self.autostart_var.set(actual)
        self.saved.configure(text="starts with Windows" if actual
                             else "will not start with Windows")

    def _toggle_bar(self) -> None:
        self.cfg.show_bar = self.bar_var.get()
        self._ensure_bar() if self.cfg.show_bar else self._destroy_bar()
        self._mark_dirty()

    # ---------------------------------------------------------------- rebind

    def _rebind(self) -> None:
        """Capture the next key pressed rather than making him type its name."""
        from pynput import keyboard

        dlg = tk.Toplevel(self)
        dlg.title("Rebind")
        dlg.configure(bg=theme.BG)
        dlg.geometry("360x160")
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.grab_set()
        ttk.Label(dlg, text="Press the key you want", style="Title.TLabel").pack(
            pady=(38, 8))
        ttk.Label(dlg, text="Escape to cancel", style="Muted.TLabel").pack()

        captured: dict[str, str] = {}

        def on_press(key) -> bool:  # noqa: ANN001
            if key == keyboard.Key.esc:
                return False
            name = getattr(key, "name", None) or getattr(key, "char", None)
            if not name:
                return True
            captured["key"] = name
            return False

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

        def finish() -> None:
            if listener.running:
                self.after(60, finish)
                return
            dlg.grab_release()
            dlg.destroy()
            key = captured.get("key")
            if key:
                self.cfg.hotkey = key
                self.hotkey_label.configure(text=self._pretty_key(key))
                self._mark_dirty()

        self.after(60, finish)

    # ------------------------------------------------------------------ save

    def _collect(self) -> dict:
        saved: dict = {}
        if CONFIG_PATH.is_file():
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        label = self.device_var.get()
        saved["input_device"] = (None if label.startswith("System")
                                 else int(label.split("]")[0].lstrip("[")))
        saved["model"] = self.model_var.get().split(" - ")[0]
        saved["hotkey"] = self.cfg.hotkey
        saved["sound_feedback"] = self.beep_var.get()
        saved["trailing_space"] = self.space_var.get()
        saved["apply_corrections"] = self.fix_var.get()
        saved["show_bar"] = self.bar_var.get()
        saved["vad"] = self.vad_var.get()
        saved["beam_size"] = int(self.beam_var.get())
        saved["best_of"] = int(self.beam_var.get())
        try:
            saved["idle_unload_seconds"] = max(0.0, float(self.idle_var.get()) * 60)
        except ValueError:
            saved["idle_unload_seconds"] = self.cfg.idle_unload_seconds

        saved["vocabulary"] = [w.strip() for w in
                               self.vocab_text.get("1.0", "end").splitlines() if w.strip()]
        fixes: dict[str, str] = {}
        for line in self.fix_text.get("1.0", "end").splitlines():
            if "=" not in line:
                continue
            wrong, right = line.split("=", 1)
            if wrong.strip() and right.strip():
                fixes[wrong.strip().lower()] = right.strip()
        saved["corrections"] = fixes
        return saved

    def _save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self._collect(), indent=2), encoding="utf-8")
        self._dirty = False
        self.save_btn.configure(state="disabled")
        self.saved.configure(text=f"saved at {datetime.now():%H:%M}")
        self._restart_engine()

    # ------------------------------------------------------------- self test

    def _run_selftest(self) -> None:
        if not messagebox.askokcancel(
                "Self test",
                "This loads the model, transcribes four test sentences and types a "
                "line into its own window. It takes about a minute.\n\n"
                "Dictation stops while it runs."):
            return
        self._stop_engine()
        cmd = ([sys.executable, "--self-test"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "sayso", "--self-test"])
        subprocess.Popen(cmd, cwd=str(DATA_DIR),
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))

    # ------------------------------------------------------------ tray/close

    def _hide(self) -> None:
        from .tray import Tray

        self.withdraw()
        if self.tray is None:
            self.tray = Tray(on_quit=lambda: self.after(0, self._on_close),
                             on_preload=self._tray_preload,
                             on_unload=self._tray_unload,
                             on_copy_last=self._copy_last,
                             on_show=lambda: self.after(0, self._show))
            threading.Thread(target=self.tray.run, daemon=True).start()

    def _tray_preload(self) -> None:
        if self.engine is not None:
            threading.Thread(target=self.engine.preload, daemon=True).start()

    def _tray_unload(self) -> None:
        if self.engine is not None:
            self.engine.transcriber.unload()

    def _show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close(self) -> None:
        if self._dirty and not messagebox.askokcancel(
                "Unsaved changes", "Close without saving your changes?"):
            return
        if self.tray is not None:
            self.tray.stop()
        self._stop_engine()
        self.destroy()


def run() -> int:
    SaysoWindow().mainloop()
    return 0
