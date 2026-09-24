"""One copy at a time, and start with Windows.

Four copies were running at once during the first day's testing, each holding
its own Whisper model and its own keyboard hook on an 8 GB card. A named mutex
is the cheap Windows answer: the second launch finds the first, waves at it,
and exits.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

from .config import DATA_DIR

ERROR_ALREADY_EXISTS = 183
MUTEX_NAME = "Local\\SaysoSingleInstance"

# A flag file rather than a window message: Tk gives no clean way to hook
# WM_ messages, and the window already polls on a timer for status updates.
WAKE_FLAG = DATA_DIR / "show-window.flag"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Sayso"

_mutex = None


def claim() -> bool:
    """True if this process is the only copy. False means one already runs."""
    global _mutex
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        log.info("another copy is already running")
        return False
    return True


def wake_existing() -> None:
    """Ask the copy that is already running to show its window."""
    try:
        WAKE_FLAG.write_text("show", encoding="utf-8")
    except OSError:
        log.exception("could not signal the running copy")


def take_wake_request() -> bool:
    """True once per request from a second launch. Clears the flag."""
    try:
        if WAKE_FLAG.is_file():
            WAKE_FLAG.unlink()
            return True
    except OSError:
        pass
    return False


# --------------------------------------------------------------- autostart


def _command() -> str:
    """The command Windows should run at login."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    root = Path(__file__).resolve().parent.parent
    return f'"{sys.executable}" -m sayso'


def start_menu_path() -> Path:
    base = os.environ.get("APPDATA", "")
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Sayso.lnk")


def install_start_menu() -> bool:
    """Put a shortcut in the Start Menu so Windows search can find it.

    Windows only indexes what is in the Start Menu; an exe sitting in a folder
    is invisible to search no matter what it is called. Creating a .lnk needs
    COM, so this goes through PowerShell rather than adding pywin32 for one
    call.
    """
    if not getattr(sys, "frozen", False):
        return False            # a dev checkout has nothing worth pinning
    link = start_menu_path()
    if link.is_file():
        return True
    target = sys.executable
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
        "$s.TargetPath = '%s'; "
        "$s.WorkingDirectory = '%s'; "
        "$s.Description = 'Local voice dictation'; "
        "$s.Save()" % (link, target, str(Path(target).parent))
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return done.returncode == 0 and link.is_file()
    except (OSError, subprocess.SubprocessError):
        log.exception("could not create the Start Menu shortcut")
        return False


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """Add or remove the login entry. Returns the state actually achieved."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return enabled
    except OSError:
        log.exception("could not change the autostart entry")
        return autostart_enabled()
