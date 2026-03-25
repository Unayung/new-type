"""
new-type — voice dictation daemon

Usage:
  uv run main.py daemon    # Start the background daemon (with tray icon)
  uv run main.py start     # Begin recording
  uv run main.py stop      # Stop recording, transcribe, inject text
  uv run main.py toggle    # Start if idle, stop if recording
  uv run main.py status    # Show current state
  uv run main.py devices   # List audio input devices

The daemon listens on a Unix socket and handles start/stop/toggle commands.
Hyprland keybind calls `uv run main.py toggle` (or start/stop on press/release).
"""

from __future__ import annotations

import os
import signal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
import socket
import sys
import threading
from pathlib import Path


import typer
import yaml

from core.recorder import Recorder
from core.transcriber import create_backend
from core.cleanup import create_cleanup
from core import context

app = typer.Typer(help="new-type voice dictation")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Status indicator — writes JSON for Waybar custom module
# ---------------------------------------------------------------------------

STATUS_FILE = Path("/tmp/new-type-status.json")


class StatusIndicator:
    """
    Writes status JSON to /tmp/new-type-status.json.
    Waybar custom module polls this file and renders a colored dot.
    No system tray / gi dependency needed.
    """

    def start(self) -> None:
        self.set_idle()

    def set_idle(self) -> None:
        self._write("●", "idle", "new-type: idle")

    def set_recording(self) -> None:
        self._write("●", "rec", "new-type: recording…")

    def clear(self) -> None:
        STATUS_FILE.unlink(missing_ok=True)

    def _write(self, text: str, alt: str, tooltip: str) -> None:
        import json
        STATUS_FILE.write_text(json.dumps({
            "text": text,
            "alt": alt,
            "class": alt,     # "idle" or "rec" — for CSS coloring
            "tooltip": tooltip,
        }))


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------

class Daemon:
    def __init__(self, config: dict):
        self.config = config
        rec_cfg = config.get("recording", {})
        self.mode = rec_cfg.get("mode", "toggle")  # toggle | auto_stop | hold

        auto_stop_cb = self._on_auto_stop if self.mode == "auto_stop" else None
        self.recorder = Recorder(
            sample_rate=config["audio"]["sample_rate"],
            silence_duration=rec_cfg.get("silence_duration", 1.5),
            speech_threshold=rec_cfg.get("speech_threshold", 0.02),
            min_speech_duration=rec_cfg.get("min_speech_duration", 0.5),
            no_speech_timeout=rec_cfg.get("no_speech_timeout", 5.0),
            on_auto_stop=auto_stop_cb,
        )
        self.transcriber = create_backend(config["transcription"])
        self.cleanup = create_cleanup(config["cleanup"])
        self.socket_path = config["socket"]["path"]
        self.status = StatusIndicator()
        self._lock = threading.Lock()

        cc_mode = config.get("chinese_convert")
        if cc_mode:
            from opencc import OpenCC
            self._opencc = OpenCC(cc_mode)
        else:
            self._opencc = None

    def _inject(self, text: str) -> None:
        if sys.platform == "darwin":
            from platforms.macos import inject_text
        else:
            from platforms.linux import inject_text
        inject_text(text)

    def _on_auto_stop(self, audio: "np.ndarray") -> None:
        """Called by recorder VAD with already-collected audio. Runs in a background thread."""
        self.status.set_idle()
        result = self._process_audio(audio)
        print(f"[auto-stop] {result}", flush=True)

    def handle_start(self) -> str:
        with self._lock:
            if self.recorder.is_recording:
                return "already_recording"
            self.recorder.start()
            self.status.set_recording()
            return "recording_started"

    def _process_audio(self, audio: "np.ndarray") -> str:
        """Transcribe audio and inject result. Shared by manual stop and auto-stop."""
        if audio is None or len(audio) == 0:
            return "no_audio"

        duration = len(audio) / self.config["audio"]["sample_rate"]
        if duration < 0.3:
            return "too_short"

        try:
            ctx = context.collect()
            lang = self.config["transcription"].get("language") or None
            result = self.transcriber.transcribe(audio, language=lang)

            if not result.text:
                return "empty_transcript"

            text = result.text
            if self._opencc:
                text = self._opencc.convert(text)

            cleaned = self.cleanup.clean(text, ctx)

            if not cleaned:
                return "empty_after_cleanup"

            self._inject(cleaned)
            return f"injected:{cleaned[:80]}"
        except Exception as e:
            return f"error:{e}"

    def _transcribe_and_inject(self) -> str:
        """Manual stop path: stops recorder then processes audio."""
        audio = self.recorder.stop()
        return self._process_audio(audio)

    def handle_stop(self) -> str:
        with self._lock:
            if not self.recorder.is_recording:
                return "not_recording"
            self.status.set_idle()
        return self._transcribe_and_inject()

    def handle_toggle(self) -> str:
        if self.recorder.is_recording:
            return self.handle_stop()
        return self.handle_start()

    def handle_status(self) -> str:
        state = "recording" if self.recorder.is_recording else "idle"
        return f"{state} (mode:{self.mode})"

    def run(self) -> None:
        sock_path = Path(self.socket_path)
        if sock_path.exists():
            sock_path.unlink()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(5)
        server.settimeout(1.0)

        self.status.start()
        print(f"[new-type] Daemon running. Socket: {self.socket_path}", flush=True)

        def _shutdown(sig, frame):
            print("\n[new-type] Shutting down...", flush=True)
            server.close()
            sock_path.unlink(missing_ok=True)
            self.status.clear()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                data = conn.recv(256).decode().strip()
                if data == "start":
                    response = self.handle_start()
                elif data == "stop":
                    response = self.handle_stop()
                elif data == "toggle":
                    response = self.handle_toggle()
                elif data == "status":
                    response = self.handle_status()
                else:
                    response = f"unknown_command:{data}"
                conn.sendall(response.encode())
            except Exception as e:
                try:
                    conn.sendall(f"error:{e}".encode())
                except Exception:
                    pass
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def send_command(cmd: str, socket_path: str) -> str:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(socket_path)
            s.sendall(cmd.encode())
            return s.recv(4096).decode()
    except (FileNotFoundError, ConnectionRefusedError):
        return "error:daemon_not_running — run: uv run main.py daemon"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def daemon():
    """Start the background daemon (with tray icon)."""
    config = load_config()
    d = Daemon(config)
    d.run()


@app.command()
def start():
    """Begin recording."""
    config = load_config()
    print(send_command("start", config["socket"]["path"]))


@app.command()
def stop():
    """Stop recording and inject transcribed text."""
    config = load_config()
    print(send_command("stop", config["socket"]["path"]))


@app.command()
def toggle():
    """Start if idle, stop and transcribe if recording."""
    config = load_config()
    print(send_command("toggle", config["socket"]["path"]))


@app.command()
def status():
    """Show recording state."""
    config = load_config()
    print(send_command("status", config["socket"]["path"]))


@app.command()
def devices():
    """List available audio input devices."""
    from core.recorder import list_devices
    list_devices()


if __name__ == "__main__":
    app()
