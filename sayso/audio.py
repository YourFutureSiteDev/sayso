"""Microphone capture.

Records mono float32 at 16 kHz, which is what Whisper wants, so nothing has to
be resampled later. Recording runs on sounddevice's own callback thread and
appends into a list; the main thread only touches it after the stream stops.
"""

from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate: int = 16_000, device: int | None = None) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._peak = 0.0
        self._level = 0.0

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @property
    def peak(self) -> float:
        """Loudest sample of the whole recording, used to decide the gain."""
        return self._peak

    @property
    def level(self) -> float:
        """How loud it is right now, 0 to 1, for the waveform.

        Smoothed and RMS rather than peak, because a raw peak reading makes the
        bars twitch on every consonant instead of following the voice.
        """
        return self._level

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        block = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(block)
            if block.size:
                block_peak = float(np.abs(block).max())
                if block_peak > self._peak:
                    self._peak = block_peak
                rms = float(np.sqrt(np.mean(np.square(block))))
                # Rise quickly, fall slowly: the bars follow speech instead of
                # collapsing in the gaps between words.
                weight = 0.5 if rms > self._level else 0.15
                self._level += (rms - self._level) * weight

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks = []
            self._peak = 0.0
            self._level = 0.0
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            blocksize=0,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Stop and return everything captured as one float32 array."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def abort(self) -> None:
        self.stop()


def normalise(audio: np.ndarray, target_peak: float = 0.85, max_gain: float = 8.0) -> np.ndarray:
    """Bring quiet speech up without clipping.

    Whisper is noticeably worse on audio recorded well below full scale, which
    is exactly what a desk mic at arm's length produces. Gain is capped so a
    near-silent recording does not become amplified room hiss.
    """
    if audio.size == 0:
        return audio
    peak = float(np.abs(audio).max())
    if peak < 1e-5:
        return audio
    gain = min(target_peak / peak, max_gain)
    if gain <= 1.0:
        return audio
    return np.clip(audio * gain, -1.0, 1.0)


def list_devices() -> list[tuple[int, str, int]]:
    """(index, name, max input channels) for every device that can record."""
    out = []
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            out.append((index, info["name"], info["max_input_channels"]))
    return out
