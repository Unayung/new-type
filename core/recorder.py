"""
Mic recorder — cross-platform via sounddevice.

Recording modes:
  toggle    — start() / stop() called explicitly (via keybind or command)
  hold      — same as toggle but caller manages start on press / stop on release
  auto_stop — start() begins recording; auto-stops after silence_duration seconds
              of silence following detected speech. Calls on_auto_stop(audio) callback.

VAD: uses Silero VAD (bundled in faster-whisper) for accurate speech detection.
Falls back to RMS energy if Silero is unavailable.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1

DEFAULT_SILENCE_DURATION = 0.8
DEFAULT_SPEECH_THRESHOLD = 0.5   # Silero probability threshold (0–1)
DEFAULT_MIN_SPEECH = 0.5
DEFAULT_NO_SPEECH_TIMEOUT = 5.0

# Silero VAD processes 512-sample chunks at 16kHz
SILERO_CHUNK = 512


def _load_silero():
    try:
        from faster_whisper.vad import get_vad_model
        return get_vad_model()
    except Exception:
        return None


class Recorder:
    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        silence_duration: float = DEFAULT_SILENCE_DURATION,
        speech_threshold: float = DEFAULT_SPEECH_THRESHOLD,
        min_speech_duration: float = DEFAULT_MIN_SPEECH,
        no_speech_timeout: float = DEFAULT_NO_SPEECH_TIMEOUT,
        on_auto_stop: Callable[[np.ndarray], None] | None = None,
    ):
        self.sample_rate = sample_rate
        self.silence_duration = silence_duration
        self.speech_threshold = speech_threshold
        self.min_speech_duration = min_speech_duration
        self.no_speech_timeout = no_speech_timeout
        self.on_auto_stop = on_auto_stop

        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

        # VAD state
        self._speech_detected = False
        self._silence_frames = 0.0
        self._speech_frames = 0.0
        self._total_frames = 0.0

        # Silero VAD — neural, much more accurate than RMS
        self._vad_model = _load_silero()
        self._silero_buffer = np.array([], dtype=np.float32)
        if self._vad_model:
            print("[recorder] Silero VAD loaded.", flush=True)
        else:
            print("[recorder] Silero VAD unavailable, falling back to RMS.", flush=True)

    def start(self) -> None:
        if self._recording:
            return
        self._chunks = []
        self._recording = True
        self._speech_detected = False
        self._silence_frames = 0.0
        self._speech_frames = 0.0
        self._total_frames = 0.0
        self._silero_buffer = np.array([], dtype=np.float32)
        if self._vad_model:
            try:
                self._vad_model.reset_states()
            except Exception:
                pass

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=CHANNELS,
            dtype="float32",
            blocksize=int(self.sample_rate * 0.1),  # 100ms blocks
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        """Manual stop — returns audio. Returns empty if not recording."""
        if not self._recording:
            return np.array([], dtype=np.float32)
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self._collect()

    def _collect(self) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.array([], dtype=np.float32)
            return np.concatenate(self._chunks, axis=0).flatten()

    def _is_speech(self, block: np.ndarray, block_duration: float) -> bool:
        """Returns True if speech is detected in this block."""
        if self._vad_model:
            # Feed block into Silero in 512-sample chunks
            self._silero_buffer = np.concatenate([self._silero_buffer, block.flatten()])
            prob = 0.0
            count = 0
            while len(self._silero_buffer) >= SILERO_CHUNK:
                chunk = self._silero_buffer[:SILERO_CHUNK]
                self._silero_buffer = self._silero_buffer[SILERO_CHUNK:]
                try:
                    p = self._vad_model(chunk, self.sample_rate)
                    # p may be a float or array depending on version
                    prob += float(np.mean(p)) if hasattr(p, '__len__') else float(p)
                    count += 1
                except Exception:
                    pass
            if count == 0:
                return False
            return (prob / count) >= self.speech_threshold
        else:
            # RMS fallback
            return float(np.sqrt(np.mean(block ** 2))) >= 0.02

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time: object,
        status: sd.CallbackFlags,
    ) -> None:
        if not self._recording:
            return

        with self._lock:
            self._chunks.append(indata.copy())

        if self.on_auto_stop is None:
            return

        block_duration = frames / self.sample_rate
        self._total_frames += block_duration

        if self._is_speech(indata, block_duration):
            self._speech_detected = True
            self._speech_frames += block_duration
            self._silence_frames = 0.0
        elif self._speech_detected:
            self._silence_frames += block_duration

        armed = self._speech_detected and self._speech_frames >= self.min_speech_duration
        timed_out = not self._speech_detected and self._total_frames >= self.no_speech_timeout

        if (armed and self._silence_frames >= self.silence_duration) or timed_out:
            self._recording = False
            threading.Thread(target=self._trigger_auto_stop, daemon=True).start()

    def _trigger_auto_stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        audio = self._collect()
        if self.on_auto_stop:
            self.on_auto_stop(audio)

    @property
    def is_recording(self) -> bool:
        return self._recording


def list_devices() -> None:
    print(sd.query_devices())
