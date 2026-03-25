"""
Transcriber — abstract backend for speech-to-text.

Backends:
  faster_whisper  — local, CTranslate2 (large-v3, turbo) — Linux/CUDA
  mlx_whisper     — local, Apple MLX (Metal + Neural Engine) — macOS Apple Silicon only
  whisper_cpp     — local, GGUF subprocess (belle-turbo, quantized models)
  groq            — cloud, Groq Whisper API (fastest cloud)
  openai          — cloud, OpenAI Whisper API
  assemblyai      — cloud, AssemblyAI (streaming support)
"""

from __future__ import annotations

import io
import subprocess
import tempfile
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from core.config import Config


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class TranscribeResult:
    def __init__(self, text: str, language: str = ""):
        self.text = text.strip()
        self.language = language

    def __str__(self) -> str:
        return self.text


class Backend(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        ...


# ---------------------------------------------------------------------------
# faster-whisper (local)
# ---------------------------------------------------------------------------

class FasterWhisperBackend(Backend):
    """Runs Whisper locally via CTranslate2. Supports: large-v3, turbo, small, medium, etc."""

    MODEL_MAP = {
        "large-v3": "large-v3",
        "turbo": "turbo",
        "small": "small",
        "medium": "medium",
        "base": "base",
        "tiny": "tiny",
    }

    def __init__(
        self,
        model: str = "turbo",
        device: str = "auto",
        compute_type: str = "auto",
        initial_prompt: str | None = None,
        hallucination_silence_threshold: float | None = None,
    ):
        from faster_whisper import WhisperModel

        resolved = self.MODEL_MAP.get(model, model)
        print(f"[transcriber] Loading faster-whisper model: {resolved} on {device}")
        self._model = WhisperModel(resolved, device=device, compute_type=compute_type)
        self._initial_prompt = initial_prompt
        self._hallucination_silence_threshold = hallucination_silence_threshold
        print("[transcriber] Model loaded.")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        kwargs: dict = {}
        if self._initial_prompt:
            kwargs["initial_prompt"] = self._initial_prompt
        if self._hallucination_silence_threshold is not None:
            kwargs["hallucination_silence_threshold"] = self._hallucination_silence_threshold

        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            **kwargs,
        )
        text = " ".join(seg.text for seg in segments)
        return TranscribeResult(text=text, language=info.language)


# ---------------------------------------------------------------------------
# mlx-whisper (Apple Silicon — Metal + Neural Engine)
# ---------------------------------------------------------------------------

class MlxWhisperBackend(Backend):
    """
    Runs Whisper on Apple Silicon via MLX (Metal + Neural Engine).
    Fastest local option on M-series Macs.

    Install: uv add mlx-whisper
    Models: mlx-community/whisper-turbo (fast) | mlx-community/whisper-large-v3-turbo (best)
    """

    DEFAULT_REPO = "mlx-community/whisper-large-v3-turbo"

    MODEL_MAP = {
        "turbo": "mlx-community/whisper-turbo",
        "large-v3": "mlx-community/whisper-large-v3",
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
        "small": "mlx-community/whisper-small",
        "medium": "mlx-community/whisper-medium",
        "base": "mlx-community/whisper-base",
        "tiny": "mlx-community/whisper-tiny",
    }

    def __init__(
        self,
        model: str = "large-v3-turbo",
        initial_prompt: str | None = None,
    ):
        self._repo = self.MODEL_MAP.get(model, model)
        self._initial_prompt = initial_prompt
        print(f"[transcriber] MLX Whisper model: {self._repo} (downloads on first use)")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        import mlx_whisper

        kwargs: dict = {"verbose": False}
        if language:
            kwargs["language"] = language
        if self._initial_prompt:
            kwargs["initial_prompt"] = self._initial_prompt

        result = mlx_whisper.transcribe(audio, path_or_hf_repo=self._repo, **kwargs)
        return TranscribeResult(text=result["text"], language=result.get("language", ""))


# ---------------------------------------------------------------------------
# whisper.cpp subprocess (GGUF — for belle-turbo and quantized models)
# ---------------------------------------------------------------------------

class WhisperCppBackend(Backend):
    """
    Runs whisper.cpp as a subprocess. Requires whisper.cpp binary installed.
    Best for GGUF quantized models like belle-turbo (q5_0).

    Install: https://github.com/ggerganov/whisper.cpp
    Arch: yay -S whisper.cpp
    """

    def __init__(self, model_path: str, binary: str = "whisper-cpp"):
        self.model_path = Path(model_path).expanduser()
        self.binary = binary
        if not self.model_path.exists():
            raise FileNotFoundError(f"GGUF model not found: {self.model_path}")

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
            _write_wav(f, audio, sample_rate)

        cmd = [
            self.binary,
            "-m", str(self.model_path),
            "-f", wav_path,
            "--output-txt",
            "--no-prints",
        ]
        if language:
            cmd += ["-l", language]

        result = subprocess.run(cmd, capture_output=True, text=True)
        Path(wav_path).unlink(missing_ok=True)

        if result.returncode != 0:
            raise RuntimeError(f"whisper.cpp error: {result.stderr}")

        return TranscribeResult(text=result.stdout.strip())


# ---------------------------------------------------------------------------
# Cloud backends
# ---------------------------------------------------------------------------

class GroqBackend(Backend):
    """Groq Whisper API — fastest cloud option, generous free tier."""

    def __init__(self, api_key: str, model: str = "whisper-large-v3-turbo"):
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        import httpx

        wav_bytes = _audio_to_wav_bytes(audio, sample_rate)
        with httpx.Client() as client:
            response = client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": self.model, **({"language": language} if language else {})},
                timeout=30,
            )
            response.raise_for_status()
        return TranscribeResult(text=response.json()["text"])


class OpenAIBackend(Backend):
    """OpenAI Whisper API."""

    def __init__(self, api_key: str, model: str = "whisper-1"):
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        import httpx

        wav_bytes = _audio_to_wav_bytes(audio, sample_rate)
        with httpx.Client() as client:
            response = client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"model": self.model, **({"language": language} if language else {})},
                timeout=30,
            )
            response.raise_for_status()
        return TranscribeResult(text=response.json()["text"])


class AssemblyAIBackend(Backend):
    """AssemblyAI — good for longer recordings."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe(self, audio: np.ndarray, sample_rate: int = 16000, language: str | None = None) -> TranscribeResult:
        import httpx

        wav_bytes = _audio_to_wav_bytes(audio, sample_rate)
        headers = {"authorization": self.api_key}

        with httpx.Client(timeout=60) as client:
            upload = client.post(
                "https://api.assemblyai.com/v2/upload",
                headers=headers,
                content=wav_bytes,
            )
            upload.raise_for_status()
            upload_url = upload.json()["upload_url"]

            transcript = client.post(
                "https://api.assemblyai.com/v2/transcript",
                headers=headers,
                json={"audio_url": upload_url, **({"language_code": language} if language else {})},
            )
            transcript.raise_for_status()
            transcript_id = transcript.json()["id"]

            import time
            while True:
                polling = client.get(
                    f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                    headers=headers,
                )
                polling.raise_for_status()
                status = polling.json()["status"]
                if status == "completed":
                    return TranscribeResult(text=polling.json()["text"])
                if status == "error":
                    raise RuntimeError(f"AssemblyAI error: {polling.json()['error']}")
                time.sleep(1)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(config: dict) -> Backend:
    """
    config example:
      {"backend": "faster_whisper", "model": "turbo"}
      {"backend": "mlx_whisper", "model": "large-v3-turbo"}
      {"backend": "whisper_cpp", "model_path": "~/.local/share/new-type/belle-turbo.gguf"}
      {"backend": "groq", "api_key": "...", "model": "whisper-large-v3-turbo"}
      {"backend": "openai", "api_key": "..."}
      {"backend": "assemblyai", "api_key": "..."}
    """
    backend = config["backend"]

    if backend == "faster_whisper":
        return FasterWhisperBackend(
            model=config.get("model", "turbo"),
            device=config.get("device", "auto"),
            compute_type=config.get("compute_type", "auto"),
            initial_prompt=config.get("initial_prompt"),
            hallucination_silence_threshold=config.get("hallucination_silence_threshold"),
        )
    elif backend == "mlx_whisper":
        return MlxWhisperBackend(
            model=config.get("model", "large-v3-turbo"),
            initial_prompt=config.get("initial_prompt"),
        )
    elif backend == "whisper_cpp":
        return WhisperCppBackend(
            model_path=config["model_path"],
            binary=config.get("binary", "whisper-cpp"),
        )
    elif backend == "groq":
        return GroqBackend(api_key=config["api_key"], model=config.get("model", "whisper-large-v3-turbo"))
    elif backend == "openai":
        return OpenAIBackend(api_key=config["api_key"], model=config.get("model", "whisper-1"))
    elif backend == "assemblyai":
        return AssemblyAIBackend(api_key=config["api_key"])
    else:
        raise ValueError(f"Unknown backend: {backend}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav(f: io.RawIOBase, audio: np.ndarray, sample_rate: int) -> None:
    with wave.open(f, "wb") as wav:  # type: ignore[arg-type]
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes((audio * 32767).astype(np.int16).tobytes())


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes((audio * 32767).astype(np.int16).tobytes())
    return buf.getvalue()
