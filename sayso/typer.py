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


def type_text(text: str, delay: float = 0.004) -> int:
    """Send text to the focused window. Returns the character count sent."""
    if not text:
        return 0
    sent = 0
    for char in text:
        if char == "\n":
            # Unicode newlines are ignored by most edit controls; send Return.
            press_key(0x0D)
            sent += 1
            if delay:
                time.sleep(delay)
            continue
        units = _codes(char)
        events = [_unit(code, up) for code in units for up in (False, True)]
        array = (INPUT * len(events))(*events)
        written = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if written != len(events):
            raise ctypes.WinError(ctypes.get_last_error())
        sent += 1
        if delay:
            time.sleep(delay)
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
