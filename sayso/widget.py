"""The floating bar: cancel, live waveform, confirm.

Hidden until a dictation starts. It appears at the bottom centre, follows your
voice, and goes away once the words have been typed.

**Why it is drawn this way.** Electron apps get bars like this for free, because
Chromium composites it with true per-pixel alpha and its rounded corners are
perfectly smooth. Tk cannot do that. Colour keying (`-transparentcolor`) was
tried first and looked blurry, because anti-aliased edge pixels blend toward
black and then have to be forced opaque, ringing the pill with a dark halo.

So the bar is a layered window: every frame is rendered with PIL at 3x, shrunk,
and handed to `UpdateLayeredWindow` as premultiplied BGRA. Windows composites
it against the desktop with real alpha, which is the same quality Chromium
gives and costs about a millisecond a frame at this size.

Two other things are easy to break:

- **It must not keep focus.** WS_EX_NOACTIVATE covers most of it, and
  `focus.Target` puts the real window back before anything is typed. Tk
  activates its own toplevel on a button press whatever the style says.
- **Tk must not paint it.** A layered window painted through
  UpdateLayeredWindow ignores its own WM_PAINT, which is why there is no canvas
  here and the geometry is all in `_render`.
"""

from __future__ import annotations

import ctypes
import math
import tkinter as tk
from ctypes import wintypes

import numpy as np
from PIL import Image, ImageDraw

# A small pill, roughly 132 by 40: big enough to read, small enough to ignore.
WIDTH, HEIGHT = 132, 40
MARGIN_BOTTOM = 90
PAD = 5
RADIUS = 13
BARS = 11
BAR_W = 2
BAR_GAP = 3
SS = 3                       # supersample factor for the rendered frame

PILL = (25, 28, 33, 242)
CANCEL_BG = (43, 48, 56, 255)
CANCEL_FG = (201, 207, 216, 255)
CONFIRM_BG = (243, 245, 248, 255)
CONFIRM_FG = (18, 21, 26, 255)
WAVE_IDLE = (93, 100, 110, 255)
WAVE_LIVE = (243, 245, 248, 255)
WAVE_BUSY = (230, 166, 46, 255)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GA_ROOT = 2
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _get_long, _set_long = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
    _get_long.restype = ctypes.c_longlong
    _set_long.restype = ctypes.c_longlong
    _set_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_longlong)
else:  # pragma: no cover - this machine is 64 bit
    _get_long, _set_long = user32.GetWindowLongW, user32.SetWindowLongW


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _declare() -> None:
    """Give ctypes the real signatures for the GDI calls.

    Without this, ctypes assumes every argument is a C int, and a 64-bit
    HBITMAP gets truncated: `SelectObject` fails with "int too long to
    convert" and nothing is ever drawn.
    """
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    user32.UpdateLayeredWindow.argtypes = (
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
        ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
        ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
        ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
    user32.UpdateLayeredWindow.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.CreateDIBSection.argtypes = (
        wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)


_declare()


class Surface:
    """A reusable 32-bit DIB that UpdateLayeredWindow draws from."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.screen_dc = user32.GetDC(0)
        self.dc = gdi32.CreateCompatibleDC(self.screen_dc)

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height      # negative: top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0       # BI_RGB

        self.bits = ctypes.c_void_p()
        self.bitmap = gdi32.CreateDIBSection(self.dc, ctypes.byref(info), 0,
                                             ctypes.byref(self.bits), None, 0)
        self.old = gdi32.SelectObject(self.dc, self.bitmap)

    def push(self, hwnd: int, image: Image.Image) -> None:
        """Composite an RGBA image onto the desktop as this window."""
        rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
        alpha = rgba[:, :, 3].astype(np.uint16)
        # Windows wants premultiplied alpha; anything else fringes the edges.
        bgra = np.empty_like(rgba)
        for dst, src in ((0, 2), (1, 1), (2, 0)):
            bgra[:, :, dst] = (rgba[:, :, src].astype(np.uint16) * alpha // 255)
        bgra[:, :, 3] = rgba[:, :, 3]
        ctypes.memmove(self.bits, bgra.tobytes(), bgra.nbytes)

        size = wintypes.SIZE(self.width, self.height)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(hwnd, self.screen_dc, None, ctypes.byref(size),
                                   self.dc, ctypes.byref(src), 0,
                                   ctypes.byref(blend), ULW_ALPHA)

    def close(self) -> None:
        gdi32.SelectObject(self.dc, self.old)
        gdi32.DeleteObject(self.bitmap)
        gdi32.DeleteDC(self.dc)
        user32.ReleaseDC(0, self.screen_dc)


class Bar(tk.Toplevel):
    """The floating control bar. Callbacks run on the UI thread."""

    def __init__(self, master: tk.Misc, on_toggle, on_confirm, on_cancel,  # noqa: ANN001
                 level_source) -> None:  # noqa: ANN001
        super().__init__(master)
        self.on_toggle = on_toggle
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.level_source = level_source

        self.state_name = "idle"
        self._levels = [0.09] * BARS
        self._phase = 0.0
        self._visible = False
        self._surface: Surface | None = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{WIDTH}x{HEIGHT}+{self._x()}+{self._y()}")
        self.withdraw()

        self.bind("<ButtonRelease-1>", self._release)
        self.after(10, self._prepare)

    # ------------------------------------------------------------- placement

    def _x(self) -> int:
        return (self.winfo_screenwidth() - WIDTH) // 2

    def _y(self) -> int:
        return self.winfo_screenheight() - HEIGHT - MARGIN_BOTTOM

    def _prepare(self) -> None:
        """Make it layered and unfocusable, then draw the first frame."""
        hwnd = self.winfo_id()
        root = user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        for handle in {root, hwnd}:
            style = _get_long(handle, GWL_EXSTYLE)
            _set_long(handle, GWL_EXSTYLE,
                      style | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self._hwnd = root
        self._surface = Surface(WIDTH, HEIGHT)
        self._animate()

    # ------------------------------------------------------------ visibility

    def show(self) -> None:
        if self._visible:
            return
        self._visible = True
        self.geometry(f"{WIDTH}x{HEIGHT}+{self._x()}+{self._y()}")
        self.deiconify()
        self.attributes("-topmost", True)

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self.withdraw()

    @property
    def visible(self) -> bool:
        return self._visible

    def set_state(self, state: str) -> None:
        """idle, recording or busy."""
        self.state_name = state

    # --------------------------------------------------------------- drawing

    def _render(self) -> Image.Image:
        w, h = WIDTH * SS, HEIGHT * SS
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        mid = h / 2

        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=PILL)

        cx = (PAD + RADIUS) * SS
        d.ellipse((cx - RADIUS * SS, mid - RADIUS * SS,
                   cx + RADIUS * SS, mid + RADIUS * SS), fill=CANCEL_BG)
        arm = 4 * SS
        for x0, y0, x1, y1 in ((cx - arm, mid - arm, cx + arm, mid + arm),
                               (cx - arm, mid + arm, cx + arm, mid - arm)):
            d.line((x0, y0, x1, y1), fill=CANCEL_FG, width=2 * SS)

        tx = (WIDTH - PAD - RADIUS) * SS
        d.ellipse((tx - RADIUS * SS, mid - RADIUS * SS,
                   tx + RADIUS * SS, mid + RADIUS * SS), fill=CONFIRM_BG)
        d.line((tx - 5 * SS, mid + 0.5 * SS, tx - 1.5 * SS, mid + 4 * SS),
               fill=CONFIRM_FG, width=2 * SS)
        d.line((tx - 1.5 * SS, mid + 4 * SS, tx + 5.5 * SS, mid - 4.5 * SS),
               fill=CONFIRM_FG, width=2 * SS)

        colour = {"recording": WAVE_LIVE, "busy": WAVE_BUSY}.get(
            self.state_name, WAVE_IDLE)
        span = (BARS * BAR_W + (BARS - 1) * BAR_GAP) * SS
        start = (w - span) / 2
        limit = h / 2 - PAD * SS - 3 * SS
        for i, level in enumerate(self._levels):
            x = start + i * (BAR_W + BAR_GAP) * SS + BAR_W * SS / 2
            half = max(1.5 * SS, min(limit, level * limit))
            d.line((x, mid - half, x, mid + half), fill=colour, width=BAR_W * SS)

        return img.resize((WIDTH, HEIGHT), Image.LANCZOS)

    # ------------------------------------------------------------- animation

    def _animate(self) -> None:
        self._phase += 0.42
        if self.state_name == "recording":
            level = max(0.0, min(1.0, self.level_source() * 3.4))
            # Tallest in the middle and tapering out, which reads as a voice
            # rather than a bar chart.
            self._levels = [
                max(0.07, level * math.sin(math.pi * (i + 0.5) / BARS) ** 0.6
                    * (0.72 + 0.28 * math.sin(self._phase + i * 0.85)))
                for i in range(BARS)
            ]
        elif self.state_name == "busy":
            self._levels = [
                0.16 + 0.4 * (1 + math.sin(self._phase * 1.4 - i * 0.5)) / 2
                for i in range(BARS)
            ]
        else:
            self._levels = [0.09] * BARS

        if self._visible and self._surface is not None:
            self._surface.push(self._hwnd, self._render())
        self.after(40, self._animate)

    # ---------------------------------------------------------------- clicks

    def _release(self, event) -> None:  # noqa: ANN001
        if event.x <= PAD + RADIUS * 2:
            self.on_cancel()
        elif event.x >= WIDTH - PAD - RADIUS * 2:
            self.on_confirm()
        else:
            self.on_toggle()

    def destroy(self) -> None:
        if self._surface is not None:
            self._surface.close()
            self._surface = None
        super().destroy()
