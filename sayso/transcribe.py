"""Whisper transcription, tuned for accuracy rather than speed.

The model is loaded on first use and dropped again after an idle period, so a
dictation app does not sit on 3 GB of a small card while other GPU work
wants it.
"""

from __future__ import annotations

import gc
import logging
import threading
import time

import numpy as np

from . import cuda_paths

cuda_paths.register()

from faster_whisper import WhisperModel  # noqa: E402  (must follow cuda_paths)

from .config import Config  # noqa: E402

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._model: WhisperModel | None = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None

    # -- model lifecycle ---------------------------------------------------

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> WhisperModel:
        with self._lock:
            if self._model is None:
                log.info("loading %s on %s (%s)", self.cfg.model, self.cfg.device, self.cfg.compute_type)
                started = time.monotonic()
                self._model = self._build()
                log.info("model ready in %.1fs", time.monotonic() - started)
                self._start_reaper()
            self._last_used = time.monotonic()
            return self._model

    def _build(self) -> WhisperModel:
        try:
            return WhisperModel(
                self.cfg.model,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
            )
        except Exception as exc:  # pragma: no cover - depends on the machine
            if self.cfg.device != "cuda":
                raise
            log.warning("cuda load failed (%s); falling back to cpu int8", exc)
            self.cfg.device = "cpu"
            self.cfg.compute_type = "int8"
            return WhisperModel(self.cfg.model, device="cpu", compute_type="int8")

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            log.info("unloading model after idle")
            self._model = None
        gc.collect()

    def _start_reaper(self) -> None:
        if self.cfg.idle_unload_seconds <= 0 or self._reaper is not None:
            return

        def run() -> None:
            while not self._stop.wait(5.0):
                if self._model is None:
                    continue
                if time.monotonic() - self._last_used > self.cfg.idle_unload_seconds:
                    self.unload()

        self._reaper = threading.Thread(target=run, name="sayso-reaper", daemon=True)
        self._reaper.start()

    def close(self) -> None:
        self._stop.set()
        self.unload()

    # -- transcription -----------------------------------------------------

    def transcribe(self, audio: np.ndarray) -> str:
        """Return exactly what was said, with Whisper's own punctuation."""
        if audio.size == 0:
            return ""
        model = self.load()
        cfg = self.cfg

        vad_params = None
        if cfg.vad:
            vad_params = {
                "threshold": cfg.vad_threshold,
                "min_speech_duration_ms": cfg.vad_min_speech_ms,
                "min_silence_duration_ms": cfg.vad_min_silence_ms,
                "speech_pad_ms": cfg.vad_speech_pad_ms,
            }

        segments, _info = model.transcribe(
            audio,
            language=cfg.language,
            task="transcribe",
            beam_size=cfg.beam_size,
            best_of=cfg.best_of,
            patience=cfg.patience,
            temperature=list(cfg.temperature_fallback),
            compression_ratio_threshold=cfg.compression_ratio_threshold,
            log_prob_threshold=cfg.log_prob_threshold,
            no_speech_threshold=cfg.no_speech_threshold,
            condition_on_previous_text=cfg.condition_on_previous_text,
            initial_prompt=cfg.initial_prompt,
            hotwords=" ".join(cfg.vocabulary) if cfg.vocabulary else None,
            vad_filter=cfg.vad,
            vad_parameters=vad_params,
            word_timestamps=False,
        )
        self._last_used = time.monotonic()
        return "".join(segment.text for segment in segments).strip()
