"""Ties the pieces together: hotkey -> microphone -> Whisper -> keystrokes."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .audio import Recorder, normalise, save_wav
from .config import Config
from .focus import Target
from .history import History
from .hotkey import PushToTalk
from .postprocess import finalise
from .transcribe import Transcriber
from .typer import type_text

log = logging.getLogger(__name__)

try:  # optional: a short beep on start/stop so you know it heard you
    import winsound
except ImportError:  # pragma: no cover - windows only
    winsound = None


@dataclass
class Result:
    text: str
    seconds: float
    audio_seconds: float


class Sayso:
    """State machine for one dictation at a time.

    Recording happens on the hotkey thread; transcription runs on a single
    worker so two quick dictations queue up instead of fighting over the GPU.
    """

    def __init__(self, cfg: Config, on_status: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.on_status = on_status or (lambda _s: None)
        self.recorder = Recorder(cfg.sample_rate, cfg.input_device)
        self.transcriber = Transcriber(cfg)
        self.target = Target()
        self.history = History(keep_days=cfg.keep_history_days)
        self.hotkey = PushToTalk(cfg.hotkey, self._start, self._stop, cfg.tap_latch_seconds)
        self._jobs: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self.last: Result | None = None
        # The last handful in memory, for the window. `self.history` is the
        # durable store on disk.
        self.recent: list[Result] = []

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._worker = threading.Thread(target=self._run, name="sayso-worker", daemon=True)
        self._worker.start()
        self.target.start()
        self.hotkey.start()
        self.status("ready")

    def close(self) -> None:
        self._stopping.set()
        self._jobs.put(None)
        self.target.stop()
        self.hotkey.stop()
        self.recorder.abort()
        self.transcriber.close()

    def status(self, text: str) -> None:
        log.info("status: %s", text)
        self.on_status(text)

    def preload(self) -> None:
        """Warm the model so the first dictation is not the slow one."""
        self.status("loading model")
        self.transcriber.load()
        self.status("ready")

    # -- controls ----------------------------------------------------------
    #
    # The hotkey drives _start/_stop directly. The floating bar uses the public
    # names below, which is also why cancel exists: a key you let go of has no
    # way to say "forget it", but a button next to a tick does.

    @property
    def active(self) -> bool:
        return self.recorder.recording

    def begin(self) -> None:
        self._start()

    def finish(self) -> None:
        self._stop()

    def toggle(self) -> None:
        self._stop() if self.recorder.recording else self._start()

    def cancel(self) -> None:
        """Throw away whatever is being recorded without transcribing it."""
        if not self.recorder.recording:
            return
        self.recorder.stop()
        self._beep(440, 80)
        self.status("cancelled")

    @property
    def level(self) -> float:
        """Loudest sample so far this recording, for the level meter."""
        return self.recorder.peak

    # -- hotkey callbacks --------------------------------------------------

    def _start(self) -> None:
        if self.recorder.recording:
            return
        self._beep(880, 60)
        self.recorder.start()
        self.status("recording")

    def _stop(self) -> None:
        if not self.recorder.recording:
            return
        audio = self.recorder.stop()
        self._beep(660, 60)
        seconds = audio.size / self.cfg.sample_rate
        if seconds < self.cfg.min_seconds:
            self.status("too short, ignored")
            return
        if seconds > self.cfg.max_seconds:
            audio = audio[: int(self.cfg.max_seconds * self.cfg.sample_rate)]
            seconds = self.cfg.max_seconds
        self.status("transcribing")
        self._jobs.put((audio, seconds))

    # -- worker ------------------------------------------------------------

    def _run(self) -> None:
        while not self._stopping.is_set():
            job = self._jobs.get()
            if job is None:
                break
            audio, seconds = job
            try:
                self._handle(audio, seconds)
            except Exception:
                log.exception("transcription failed")
                self.status("error, see the log")

    def _handle(self, audio, audio_seconds: float) -> None:
        started = time.monotonic()
        audio = normalise(audio, self.cfg.target_peak, self.cfg.max_gain)
        if self.cfg.keep_last_recording:
            save_wav(audio, self.cfg.sample_rate)
        raw = self.transcriber.transcribe(audio)
        text = finalise(
            raw,
            corrections=self.cfg.corrections if self.cfg.apply_corrections else None,
            casing=self.cfg.casing if self.cfg.apply_corrections else None,
            fillers=self.cfg.strip_fillers,
            trailing_space=self.cfg.trailing_space,
        )
        elapsed = time.monotonic() - started
        if not text.strip():
            self.status("nothing heard")
            return
        # Put the box he was typing in back in front first. Clicking the
        # floating bar moves focus to it, and keystrokes follow focus.
        self.target.restore()
        type_text(text, self.cfg.type_delay)
        result = Result(text=text, seconds=elapsed, audio_seconds=audio_seconds)
        self.last = result
        self.recent.append(result)
        del self.recent[:-20]
        self.history.add(text, audio_seconds, elapsed)
        self.status(f"typed {len(text)} chars in {elapsed:.1f}s")

    # -- helpers -----------------------------------------------------------

    def _beep(self, freq: int, ms: int) -> None:
        if not self.cfg.sound_feedback or winsound is None:
            return
        try:
            winsound.Beep(freq, ms)
        except RuntimeError:
            pass
