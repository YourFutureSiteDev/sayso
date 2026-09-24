"""Entry point.

  run.cmd                 tray app, hold Right Ctrl to dictate
  run.cmd --devices       list microphones with their indexes
  run.cmd --file a.wav    transcribe a file and print it (no typing)
  run.cmd --console       run without the tray, log to the terminal
  run.cmd --self-test     prove the whole chain works end to end
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from .config import Config, DATA_DIR

# Both live with the settings rather than beside the exe, because a rebuild
# wipes the dist folder.
LOG_PATH = DATA_DIR / "sayso.log"
CONSOLE_PATH = DATA_DIR / "sayso-output.txt"


def capture_output() -> None:
    """Give the frozen app somewhere to print to.

    A windowed PyInstaller build has no console, so sys.stdout is None and the
    first print() anywhere kills the app on launch. Pointing both streams at a
    file keeps --self-test and --devices usable from the exe as well.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = open(CONSOLE_PATH, "w", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_devices() -> int:
    from .audio import list_devices
    for index, name, channels in list_devices():
        print(f"  [{index:>2}] {name}  ({channels} ch)")
    print("\nSet input_device in config.json to one of those numbers.")
    return 0


def cmd_file(path: Path, cfg: Config) -> int:
    import wave
    import numpy as np
    from .audio import normalise
    from .postprocess import finalise
    from .transcribe import Transcriber

    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[width]
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    audio /= float(np.iinfo(dtype).max)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != cfg.sample_rate:
        # Linear resample is plenty: Whisper's own front end is band limited.
        target = int(round(audio.size * cfg.sample_rate / rate))
        audio = np.interp(np.linspace(0, audio.size - 1, target),
                          np.arange(audio.size), audio).astype(np.float32)

    transcriber = Transcriber(cfg)
    raw = transcriber.transcribe(normalise(audio, cfg.target_peak, cfg.max_gain))
    print(finalise(raw, corrections=cfg.corrections if cfg.apply_corrections else None,
                   casing=cfg.casing if cfg.apply_corrections else None,
                   fillers=cfg.strip_fillers, trailing_space=False))
    transcriber.close()
    return 0


def cmd_selftest(cfg: Config) -> int:
    from .selftest import run
    return run(cfg)


def cmd_console(cfg: Config) -> int:
    from .app import Sayso
    app = Sayso(cfg, on_status=lambda s: print(f"  [{s}]"))
    app.start()
    app.preload()
    print(f"\nHold {cfg.hotkey} to dictate, tap it to latch. Ctrl+C to quit.\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    app.close()
    return 0


def cmd_tray(cfg: Config) -> int:
    from .app import Sayso
    from .tray import Tray

    app: "Sayso | None" = None

    def copy_last() -> None:
        if app and app.last:
            import subprocess
            subprocess.run("clip", input=app.last.text.encode("utf-16-le"), shell=True)

    tray = Tray(
        on_quit=lambda: app and app.close(),
        on_preload=lambda: threading.Thread(target=app.preload, daemon=True).start(),
        on_unload=lambda: app.transcriber.unload(),
        on_copy_last=copy_last,
    )
    app = Sayso(cfg, on_status=tray.set_status)
    app.start()
    threading.Thread(target=app.preload, daemon=True).start()
    tray.run()
    app.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    # Before argparse: a bad flag makes argparse write to stderr, which is None
    # in a windowed build.
    capture_output()
    parser = argparse.ArgumentParser(prog="sayso", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--devices", action="store_true", help="list microphones")
    parser.add_argument("--file", type=Path, help="transcribe a wav file and print it")
    parser.add_argument("--retry", action="store_true",
                        help="re-transcribe the last recording with the current settings")
    parser.add_argument("--tray", action="store_true", help="tray icon only, no window")
    parser.add_argument("--console", action="store_true", help="no window, no tray")
    parser.add_argument("--self-test", action="store_true", help="end to end check")
    parser.add_argument("--model", help="override the model, e.g. large-v3-turbo")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    cfg = Config.load()
    cfg.write_template()
    if args.model:
        cfg.model = args.model

    if args.devices:
        return cmd_devices()
    if args.file:
        return cmd_file(args.file, cfg)
    if args.retry:
        last = DATA_DIR / "last-recording.wav"
        if not last.is_file():
            print("No saved recording yet. Dictate once, then try again.")
            return 1
        print(f"Re-running {last}\n"
              f"  vad {cfg.vad_threshold} pad {cfg.vad_speech_pad_ms}ms  "
              f"logprob {cfg.log_prob_threshold}  "
              f"no_speech {cfg.no_speech_threshold}  "
              f"context {cfg.condition_on_previous_text}\n")
        return cmd_file(last, cfg)
    if args.self_test:
        return cmd_selftest(cfg)
    if args.console:
        return cmd_console(cfg)
    if args.tray:
        return cmd_tray(cfg)

    # Only one copy: four were running at once during the first day's testing,
    # each holding a Whisper model and its own keyboard hook on an 8 GB card.
    # A second launch just brings the first one's window up.
    from . import single
    if not single.claim():
        single.wake_existing()
        return 0

    # Windows search only indexes the Start Menu, so an exe in a folder is
    # invisible to it no matter what the file is called. Harmless if it is
    # already there.
    single.install_start_menu()

    from .gui import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
