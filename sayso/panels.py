"""Rounded cards for the window.

Tk cannot draw a rounded rectangle without jagged corners, and it has no
anti-aliasing at all. So every card is a small PIL image rendered at 3x and
shrunk, cached by size and colour, then dropped on a canvas. The text goes on
the canvas over the top, which keeps it crisp because Tk renders fonts itself.

The same approach fixed the floating bar, for the same reason.
"""

from __future__ import annotations

import tkinter as tk
from functools import lru_cache

from PIL import Image, ImageDraw, ImageTk

SS = 3
_keep: list[ImageTk.PhotoImage] = []     # Tk drops images that are not referenced


@lru_cache(maxsize=64)
def _card_image(width: int, height: int, radius: int, fill: str,
                outline: str | None, bg: str) -> ImageTk.PhotoImage:
    """A rounded rectangle, flattened onto the page background.

    Flattened rather than transparent because Tk canvases do not composite
    alpha: an RGBA image would show its own black fringe.
    """
    big = Image.new("RGB", (width * SS, height * SS), bg)
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, width * SS - 1, height * SS - 1),
                           radius=radius * SS, fill=fill,
                           outline=outline, width=SS if outline else 0)
    photo = ImageTk.PhotoImage(big.resize((width, height), Image.LANCZOS))
    _keep.append(photo)
    return photo


def card(canvas: tk.Canvas, x: int, y: int, width: int, height: int, *,
         radius: int = 14, fill: str, outline: str | None = None,
         bg: str) -> int:
    """Draw a rounded card and return its canvas item id."""
    image = _card_image(width, height, radius, fill, outline, bg)
    return canvas.create_image(x, y, image=image, anchor="nw")


def recolour(canvas: tk.Canvas, item: int, width: int, height: int, *,
             radius: int, fill: str, outline: str | None, bg: str) -> None:
    """Swap a card's colour, for hover states."""
    canvas.itemconfigure(item, image=_card_image(width, height, radius, fill,
                                                 outline, bg))
