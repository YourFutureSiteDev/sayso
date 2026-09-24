"""End to end check: synthesise speech, transcribe it, score it, type it.

Uses the Windows speech synthesiser to make the test audio so the check needs
no microphone and no recording of anyone's voice. Synthetic speech is harder
for Whisper than a real person, so treat the word error rate here as a floor,
not a prediction.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

SENTENCES = [
    "Deploy the site to Cloudflare and then check the worker logs for errors.",
    "Clone the GitHub repo, run the migration, and restart the Postgres service.",
    "The endpoint returns JSON, so parse it before writing to the SQLite database.",
    "The invoice is five hundred dollars, payable on completion, due in two days.",
]


def _synthesise(text: str, path: Path) -> bool:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{path}'); "
        f"$s.Speak(@'\n{text}\n'@); "
        "$s.Dispose()"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True)
    return done.returncode == 0 and path.is_file() and path.stat().st_size > 1000


# Whisper writes numbers and currency the way a person would type them, so it
# returns "$500" for "five hundred dollars". That is a formatting choice, not a
# mishearing, and scoring it as three wrong words hides the real error rate.
# These two tables exist only for scoring; nothing here touches the output.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def _spell_numbers(words: list[str]) -> list[str]:
    """Collapse runs of number words into one digit string."""
    out: list[str] = []
    total = current = 0
    active = False

    def flush() -> None:
        nonlocal total, current, active
        if active:
            out.append(str(total + current))
            total = current = 0
            active = False

    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
            active = True
        elif word in _SCALES:
            scale = _SCALES[word]
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
            active = True
        elif word == "and" and active:
            continue
        else:
            flush()
            out.append(word)
    flush()
    return out


def _words(text: str) -> list[str]:
    text = text.replace("$", " ").replace("%", " percent ")
    keep = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in text)
    words = [w for w in keep.split() if w != "dollars"]
    return _spell_numbers(words)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = _words(reference), _words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        current = [i]
        for j, h in enumerate(hyp, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / len(ref)


def _take_foreground(hwnd: int) -> bool:
    """Make hwnd the foreground window.

    Windows refuses SetForegroundWindow from a process that does not already
    own the foreground, which is exactly the case for a script launched from a
    shell. Releasing Alt first hands this process foreground rights, which is
    the documented way round the lock.
    """
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    from .typer import INPUT, KEYBDINPUT, KEYEVENTF_KEYUP, INPUT_KEYBOARD, _INPUTUNION

    vk_menu = 0x12
    up = INPUT(type=INPUT_KEYBOARD,
               u=_INPUTUNION(ki=KEYBDINPUT(wVk=vk_menu, wScan=0,
                                           dwFlags=KEYEVENTF_KEYUP,
                                           time=0, dwExtraInfo=0)))
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(INPUT))
    user32.SetForegroundWindow(hwnd)
    user32.SetActiveWindow(hwnd)
    time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def _check_typing() -> tuple[bool, str]:
    """Type into a real focused text box and read back what arrived.

    Deliberately includes punctuation, a currency symbol, a percent sign and a
    non-ASCII dash, because those are what a naive keystroke sender mangles.
    """
    try:
        import tkinter as tk
    except ImportError:
        return False, "tkinter missing, cannot verify typing"
    from .typer import type_text

    sample = "Cloudflare Pages, $500 (50% done) — ready?"
    root = tk.Tk()
    root.title("Sayso self test")
    root.geometry("460x100+140+140")
    root.attributes("-topmost", True)
    entry = tk.Entry(root, width=64)
    entry.pack(padx=12, pady=28)
    root.update()
    root.deiconify()
    root.lift()

    got = ""
    try:
        hwnd = int(root.wm_frame(), 16)
        if not _take_foreground(hwnd):
            return False, "could not take keyboard focus"
        entry.focus_force()
        for _ in range(10):
            root.update()
            time.sleep(0.02)

        type_text(sample, delay=0.004)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            root.update()
            got = entry.get()
            if got == sample:
                break
            time.sleep(0.02)
    finally:
        root.destroy()
    return got == sample, got


def run(cfg) -> int:  # noqa: ANN001
    from .audio import list_devices, normalise
    from .postprocess import finalise
    from .transcribe import Transcriber

    print("Sayso self test\n" + "=" * 52)
    failures = 0

    devices = list_devices()
    print(f"\n1. microphones: {len(devices)} found")
    for index, name, _ch in devices[:5]:
        print(f"     [{index}] {name}")
    if not devices:
        print("     FAIL no input device")
        failures += 1

    print(f"\n2. loading {cfg.model} on {cfg.device} ({cfg.compute_type})")
    started = time.monotonic()
    transcriber = Transcriber(cfg)
    transcriber.load()
    print(f"     ok in {time.monotonic() - started:.1f}s on {transcriber.cfg.device}")
    if transcriber.cfg.device != "cuda":
        print("     WARN fell back off the GPU, dictation will be slow")

    print("\n3. transcribing synthetic speech")
    rates: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        for n, sentence in enumerate(SENTENCES, start=1):
            wav = Path(tmp) / f"s{n}.wav"
            if not _synthesise(sentence, wav):
                print(f"     [{n}] SKIP speech synthesiser unavailable")
                continue
            import wave
            import numpy as np
            with wave.open(str(wav), "rb") as fh:
                rate = fh.getframerate()
                pcm = np.frombuffer(fh.readframes(fh.getnframes()), dtype=np.int16)
            audio = pcm.astype(np.float32) / 32768.0
            if rate != cfg.sample_rate:
                target = int(round(audio.size * cfg.sample_rate / rate))
                audio = np.interp(np.linspace(0, audio.size - 1, target),
                                  np.arange(audio.size), audio).astype(np.float32)
            t0 = time.monotonic()
            raw = transcriber.transcribe(normalise(audio, cfg.target_peak, cfg.max_gain))
            took = time.monotonic() - t0
            text = finalise(raw, corrections=cfg.corrections, casing=cfg.casing,
                            trailing_space=False)
            wer = word_error_rate(sentence, text)
            rates.append(wer)
            flag = "ok  " if wer <= 0.10 else "POOR"
            audio_len = audio.size / cfg.sample_rate
            print(f"     [{n}] {flag} wer {wer:6.1%}  {took:4.1f}s for {audio_len:4.1f}s audio")
            print(f"          said:  {sentence}")
            print(f"          heard: {text}")

    if rates:
        mean = sum(rates) / len(rates)
        print(f"\n     mean word error rate {mean:.1%} over {len(rates)} sentences")
        if mean > 0.15:
            print("     FAIL accuracy below the bar")
            failures += 1
    else:
        print("     FAIL no sentences were transcribed")
        failures += 1

    print("\n4. typing into a focused text box")
    ok, got = _check_typing()
    if ok:
        print("     ok  unicode, punctuation and symbols all arrived intact")
    else:
        print(f"     FAIL got {got!r}")
        failures += 1

    transcriber.close()
    print("\n" + "=" * 52)
    print("PASS, ready to use" if failures == 0 else f"FAILED with {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    from .config import Config
    sys.exit(run(Config.load()))
