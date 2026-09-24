"""System tray icon.

The icon is drawn at runtime rather than shipped as a file, so there is no
asset to lose, and its colour is the whole status display: grey idle, red
recording, amber transcribing, green when text has just landed.
"""

from __future__ import annotations

import logging
import threading

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

COLOURS = {
    "idle": (120, 124, 130),
    "recording": (220, 64, 64),
    "busy": (230, 166, 46),
    "done": (74, 186, 116),
    "error": (200, 60, 120),
}

SIZE = 64


def _mic_icon(colour: tuple[int, int, int]) -> Image.Image:
    """A simple microphone: a rounded capsule over a stand."""
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 10, 40, 38), radius=8, fill=colour)
    d.arc((18, 24, 46, 48), start=0, end=180, fill=colour, width=4)
    d.line((32, 46, 32, 54), fill=colour, width=4)
    d.line((24, 54, 40, 54), fill=colour, width=4)
    return img


class Tray:
    def __init__(self, on_quit, on_preload, on_unload, on_copy_last,  # noqa: ANN001
                 on_show=None) -> None:  # noqa: ANN001
        self._state = "idle"
        self._detail = "ready"
        items = [
            pystray.MenuItem(lambda _i: f"Status: {self._detail}", None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
        if on_show is not None:
            # default=True makes this fire on a double click of the icon too.
            items.append(pystray.MenuItem("Open Sayso", lambda *_: on_show(),
                                          default=True))
        items += [
            pystray.MenuItem("Copy last dictation", lambda *_: on_copy_last()),
            pystray.MenuItem("Load model now", lambda *_: on_preload()),
            pystray.MenuItem("Free the GPU", lambda *_: on_unload()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: (on_quit(), self._icon.stop())),
        ]
        self._icon = pystray.Icon(
            "sayso",
            _mic_icon(COLOURS["idle"]),
            "Sayso: ready",
            menu=pystray.Menu(*items),
        )

    def set_status(self, detail: str) -> None:
        self._detail = detail
        state = "idle"
        if detail.startswith("recording"):
            state = "recording"
        elif detail.startswith(("transcribing", "loading")):
            state = "busy"
        elif detail.startswith("typed"):
            state = "done"
        elif detail.startswith("error"):
            state = "error"
        if state != self._state:
            self._state = state
            self._icon.icon = _mic_icon(COLOURS[state])
        self._icon.title = f"Sayso: {detail}"

    def run(self) -> None:
        """Blocks on the main thread, which is what pystray requires."""
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()
