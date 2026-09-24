"""Remember which window the words are meant to go into.

Clicking the floating bar moves the foreground window to the bar, even with
WS_EX_NOACTIVATE set, because Tk activates its own toplevel on a button press.
The keystrokes would then land in the bar instead of the text box.

Rather than fight Tk for it, this keeps track of the last foreground window
that was not ours and puts it back just before typing. That also covers every
other way focus can move in between speaking and the text arriving.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD))

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


class Target:
    """Polls for the foreground window and keeps the last one worth typing into."""

    def __init__(self, poll_seconds: float = 0.2) -> None:
        self.poll_seconds = poll_seconds
        self._pid = os.getpid()
        self._hwnd: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def hwnd(self) -> int | None:
        return self._hwnd

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sayso-focus",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            hwnd = user32.GetForegroundWindow()
            if hwnd and not self._is_ours(hwnd):
                self._hwnd = hwnd

    def _is_ours(self, hwnd: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == self._pid

    def restore(self) -> bool:
        """Put the remembered window back in front. True if it is there now."""
        hwnd = self._hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            return False
        if user32.GetForegroundWindow() == hwnd:
            return True

        # Windows refuses SetForegroundWindow from a process that does not
        # already own the foreground. Releasing Alt hands this process the
        # right to set it, which is the documented way round the lock.
        _release_alt()
        user32.SetForegroundWindow(hwnd)
        for _ in range(10):
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
        log.debug("could not restore focus to %s", hwnd)
        return False


def _release_alt() -> None:
    from .typer import INPUT, INPUT_KEYBOARD, KEYBDINPUT, _INPUTUNION

    up = INPUT(type=INPUT_KEYBOARD,
               u=_INPUTUNION(ki=KEYBDINPUT(wVk=VK_MENU, wScan=0,
                                           dwFlags=KEYEVENTF_KEYUP,
                                           time=0, dwExtraInfo=0)))
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
