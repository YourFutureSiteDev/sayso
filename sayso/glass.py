"""Frosted glass backdrop and panels, composited in PIL.

Tk has no blur, no gradients and no alpha between widgets, so none of this can
be done with widget styling. Instead the whole backdrop is rendered as one
image and pushed to a canvas, with the text and bars drawn as canvas items on
top where Tk still renders fonts crisply.

**The blur is free.** The aurora is drawn at about a tenth scale and upscaled
with LANCZOS, which produces a soft gradient far faster than blurring at full
size, and is why this can animate at all. Blurring an 880x660 image every
frame would not keep up; drawing an 88x66 one is nothing.

A frosted panel is then just a translucent fill over that soft backdrop, plus
a lighter top edge for the lit rim that sells the effect.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SCALE = 10          # backdrop is rendered this many times smaller
SS = 3              # supersample for panel edges


def _blob(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
          colour: tuple[int, int, int], steps: int = 14) -> None:
    """A soft radial glow, drawn as nested ellipses.

    At a tenth scale this is a handful of circles, and the upscale turns the
    banding into a smooth falloff.
    """
    for i in range(steps, 0, -1):
        t = i / steps
        radius = r * t
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     fill=colour)


def aurora(width: int, height: int, phase: float, base: str = "#0b0d11",
           blobs: tuple = ()) -> Image.Image:
    """The moving backdrop, at full size but rendered small and upscaled."""
    w, h = max(8, width // SCALE), max(8, height // SCALE)
    img = Image.new("RGB", (w, h), base)
    layer = Image.new("RGB", (w, h), base)
    draw = ImageDraw.Draw(layer)

    for i, (colour, ox, oy, radius, speed) in enumerate(blobs):
        cx = w * (ox + 0.16 * math.sin(phase * speed + i * 1.7))
        cy = h * (oy + 0.13 * math.cos(phase * speed * 0.8 + i * 2.3))
        _blob(draw, cx, cy, w * radius, colour)
        # Low blend on purpose. Anything stronger and the blobs stack into a
        # bright wash that reads as a light theme.
        img = Image.blend(img, layer, 0.28)
        layer = img.copy()
        draw = ImageDraw.Draw(layer)

    img = img.filter(ImageFilter.GaussianBlur(radius=2))
    return img.resize((max(1, width), max(1, height)), Image.LANCZOS)


# Deep and desaturated. Bright colours here turn the whole window pastel once
# the frosted panels lighten it again on top.
BLOBS = (
    ((18, 84, 66), 0.16, 0.20, 0.60, 0.31),
    ((22, 48, 96), 0.80, 0.28, 0.55, 0.24),
    ((56, 30, 86), 0.58, 0.84, 0.62, 0.19),
    ((14, 64, 74), 0.08, 0.78, 0.48, 0.27),
)
BASE = "#07090c"


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    w, h = size
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1),
                                          radius=radius * SS, fill=255)
    return big.resize((w, h), Image.LANCZOS)


def frost(base: Image.Image, box: tuple[int, int, int, int], *,
          radius: int = 16, tint: tuple[int, int, int] = (255, 255, 255),
          alpha: int = 11, border: int = 46,
          lift: int = 18, solid: tuple[int, int, int] | None = None) -> None:
    """Composite a frosted panel onto `base`, in place.

    `alpha` is the milkiness of the glass, `border` the strength of the rim
    light, and `lift` an extra brightening along the top edge, which is what
    makes a flat rectangle read as a lit pane rather than a grey box.

    `solid` fills with one flat colour instead, keeping only the rim and the
    rounded outline. Use it wherever opaque widgets have to sit on the panel:
    Tk cannot make a widget translucent, so a widget tinted to one fixed
    colour on a see-through panel reads as a grey chip floating on it. A flat
    panel the widgets can match exactly looks deliberate, and the glass still
    reads from the backdrop showing between panels.
    """
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    region = base.crop((x0, y0, x0 + w, y0 + h)).convert("RGB")

    if solid is not None:
        region = Image.new("RGB", (w, h), solid)
    else:
        # The milky fill, brighter at the top.
        pane = Image.new("RGB", (w, h), tint)
        gradient = Image.new("L", (1, h))
        for y in range(h):
            t = y / max(1, h - 1)
            gradient.putpixel((0, y), int(alpha + lift * (1 - t) ** 2))
        fill_mask = gradient.resize((w, h))
        region = Image.composite(Image.blend(region, pane, 1.0), region, fill_mask)

    # The rim: a one pixel light edge around the rounded outline.
    mask = _rounded_mask((w, h), radius)
    inner = _rounded_mask((w - 2, h - 2), max(1, radius - 1))
    ring = Image.new("L", (w, h), 0)
    ring.paste(mask, (0, 0))
    hole = Image.new("L", (w, h), 0)
    hole.paste(inner, (1, 1))
    ring = Image.eval(ring, lambda v: v)
    ring = Image.composite(Image.new("L", (w, h), 0), ring, hole)
    ring = ring.point(lambda v: int(v * border / 255))
    region = Image.composite(Image.new("RGB", (w, h), tint), region, ring)

    base.paste(region, (x0, y0), mask)
