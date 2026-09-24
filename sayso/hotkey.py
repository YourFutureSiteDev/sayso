"""Global dictation key.

Press once to start and once to stop. Holding it also works and ends the
dictation on release, so both habits do the right thing.

Everything runs on pynput's listener thread, which must stay cheap, so the
callbacks only flip state and hand the work to the caller's thread.

**A binding can fail in complete silence.** The listener starts, the key is
pressed, and nothing happens, because the name pynput reports is not the name
that was bound: Right Alt is AltGr on most layouts and arrives as `alt_gr`.
`_SAME_KEY` is what stops that, and it is why `_matches` compares against a set
rather than one key.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from pynput import keyboard

log = logging.getLogger(__name__)

_ALIASES = {
    "ctrl": "ctrl_l", "control": "ctrl_l", "rctrl": "ctrl_r", "right_ctrl": "ctrl_r",
    "alt": "alt_l", "ralt": "alt_r", "right_alt": "alt_r", "altgr": "alt_gr",
    "shift": "shift_l", "rshift": "shift_r", "win": "cmd", "super": "cmd",
}


# Names that mean the same physical key. Right Alt is AltGr on most layouts,
# and pynput reports it as alt_gr there and alt_r elsewhere.
# Binding to one and receiving the other is silent: the listener runs, the key
# is pressed, and nothing ever happens.
_SAME_KEY = [{"alt_r", "alt_gr"}]

# Windows repeats a held key, and a repeat that arrives just after a dictation
# ended starts a phantom one: a beep, a quarter second of nothing, and a "too
# short, ignored". A press this soon after a stop is never a real one.
_REPEAT_GUARD_SECONDS = 0.25


def resolve(name: str):
    """Turn a config string into the pynput key object it names."""
    key = _ALIASES.get(name.strip().lower(), name.strip().lower())
    if hasattr(keyboard.Key, key):
        return getattr(keyboard.Key, key)
    if len(key) == 1:
        return keyboard.KeyCode.from_char(key)
    raise ValueError(f"unknown hotkey {name!r}")


def accepted_names(name: str) -> set[str]:
    """Every pynput name that should count as this hotkey."""
    key = _ALIASES.get(name.strip().lower(), name.strip().lower())
    for group in _SAME_KEY:
        if key in group:
            return set(group)
    return {key}


class PushToTalk:
    def __init__(self, key_name: str, on_start: Callable[[], None],
                 on_stop: Callable[[], None], tap_latch_seconds: float = 0.4) -> None:
        self.key = resolve(key_name)
        self.key_name = key_name
        self.accepts = accepted_names(key_name)
        self.on_start = on_start
        self.on_stop = on_stop
        self.tap_latch_seconds = tap_latch_seconds
        self._listener: keyboard.Listener | None = None
        self._down_at: float | None = None
        self._active = False
        self._latched = False
        self._stopped_at = 0.0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def latched(self) -> bool:
        return self._latched

    def _matches(self, key) -> bool:  # noqa: ANN001
        if key == self.key:
            return True
        # Compare by name, and accept every name that means the same physical
        # key: Right Alt arrives as alt_gr on most layouts and alt_r on
        # others, and binding one while receiving the other fails in silence.
        return getattr(key, "name", None) in self.accepts

    def _press(self, key) -> None:  # noqa: ANN001
        if not self._matches(key) or self._down_at is not None:
            return
        now = time.monotonic()
        if now - self._stopped_at < _REPEAT_GUARD_SECONDS:
            return          # an auto-repeat trailing the press he just finished
        self._down_at = now
        if self._latched:
            return
        if not self._active:
            self._active = True
            self._safe(self.on_start)

    def _release(self, key) -> None:  # noqa: ANN001
        if not self._matches(key):
            return
        down_at, self._down_at = self._down_at, None
        if down_at is None:
            return
        held = time.monotonic() - down_at

        if self._latched:
            # A second press ends the dictation.
            self._latched = False
            self._fire_stop()
            return

        if held < self.tap_latch_seconds:
            self._latched = True  # a press: keep going until the next one
            return

        self._fire_stop()          # he held it, so it ends on release

    def _fire_stop(self) -> None:
        self._active = False
        self._stopped_at = time.monotonic()
        self._safe(self.on_stop)

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:  # keep the listener thread alive whatever happens
            log.exception("hotkey callback failed")

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()
        log.info("listening on %s (accepts %s), press to start and press to stop",
                 self.key_name, ", ".join(sorted(self.accepts)))

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
