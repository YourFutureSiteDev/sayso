"""Type text into whatever window has focus.

Uses SendInput with KEYEVENTF_UNICODE, which posts characters directly rather
than pressing keys, so it is layout independent, handles punctuation and emoji,
and never touches the clipboard.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

user32 = ctypes.WinDLL("user32", use_last_error=True)

ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def _unit(code: int, keyup: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    return INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=flags,
                                             time=0, dwExtraInfo=0)))


def _codes(char: str) -> list[int]:
    """UTF-16 code units. Anything past the BMP needs a surrogate pair."""
    point = ord(char)
    if point <= 0xFFFF:
        return [point]
    point -= 0x10000
    return [0xD800 + (point >> 10), 0xDC00 + (point & 0x3FF)]


BATCH = 400


def _send(events: list) -> None:  # noqa: ANN001
    if not events:
        return
    array = (INPUT * len(events))(*events)
    written = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if written != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def type_text(text: str, delay: float = 0.0) -> int:
    """Send text to the focused window. Returns the character count sent.

    Sent in batches rather than a character at a time. Typing 180 characters
    one by one with a 4 ms gap held the keyboard for three quarters of a
    second, and anything the user typed in that window was interleaved into
    the middle of their own dictation. One batched call injects the lot in a
    few milliseconds, so there is almost no window to collide with.

    `delay` stays as an escape hatch for an app that cannot keep up.
    """
    if not text:
        return 0
    sent = 0
    events: list = []
    for char in text:
        if char == "\n":
            # Unicode newlines are ignored by most edit controls; send Return.
            _send(events)
            events = []
            press_key(0x0D)
            sent += 1
            continue
        for code in _codes(char):
            events.append(_unit(code, False))
            events.append(_unit(code, True))
        sent += 1
        if len(events) >= BATCH:
            _send(events)
            events = []
            if delay:
                time.sleep(delay)
    _send(events)
    return sent


def press_key(vk: int) -> None:
    """Press and release a virtual key (used for Return)."""
    down = INPUT(type=INPUT_KEYBOARD,
                 u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0,
                                             time=0, dwExtraInfo=0)))
    up = INPUT(type=INPUT_KEYBOARD,
               u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP,
                                           time=0, dwExtraInfo=0)))
    array = (INPUT * 2)(down, up)
    user32.SendInput(2, array, ctypes.sizeof(INPUT))
