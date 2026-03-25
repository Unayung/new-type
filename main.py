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

import signal
from typing import TYPE_CHECKING

import numpy as np
if TYPE_CHECKING:
    pass
import socket
import sys
import threading
from pathlib import Path


import typer
import yaml

from core.recorder import Recorder
from core.transcriber import create_backend
from core.cleanup import create_cleanup
from core.hotkey import create_hotkey_listener
from core.config_ui import ConfigServer
from core import context

app = typer.Typer(help="new-type voice dictation")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Status indicator
# ---------------------------------------------------------------------------

STATUS_FILE = Path("/tmp/new-type-status.json")


class StatusIndicator:
    """
    Dual output:
    - macOS: updates the rumps menu bar app (title + status menu item)
    - Linux: writes JSON to /tmp/new-type-status.json for Waybar
    """

    def __init__(self) -> None:
        self._tray: "MenuBarApp | None" = None

    def attach_tray(self, tray: "MenuBarApp") -> None:
        self._tray = tray

    def start(self) -> None:
        self.set_idle()

    def set_idle(self) -> None:
        if self._tray:
            self._tray.set_idle()
        self._write_json("●", "idle", "new-type: idle")

    def set_recording(self) -> None:
        if self._tray:
            self._tray.set_recording()
        self._write_json("●", "rec", "new-type: recording…")

    def clear(self) -> None:
        STATUS_FILE.unlink(missing_ok=True)

    def _write_json(self, text: str, alt: str, tooltip: str) -> None:
        import json
        STATUS_FILE.write_text(json.dumps({
            "text": text, "alt": alt, "class": alt, "tooltip": tooltip,
        }))


# ---------------------------------------------------------------------------
# macOS menu bar app (rumps)
# ---------------------------------------------------------------------------

def _make_tray_icon(recording: bool) -> str:
    """
    Render a waveform icon (3 bars) to a temp PNG and return its path.
    Black on transparent → macOS auto-inverts for dark/light menu bar.
    Red bars when recording.
    """
    from PIL import Image, ImageDraw

    size = 36  # 18pt @ 2x retina
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color = (255, 59, 48, 255) if recording else (0, 0, 0, 255)
    bar_w = 5
    gap = 3
    # heights: short, tall, short — classic waveform silhouette
    heights = [14, 22, 14] if not recording else [18, 28, 18]
    total_w = 3 * bar_w + 2 * gap
    x0 = (size - total_w) // 2

    for i, h in enumerate(heights):
        x = x0 + i * (bar_w + gap)
        y = (size - h) // 2
        draw.rounded_rectangle([x, y, x + bar_w - 1, y + h - 1], radius=2, fill=color)

    path = f"/tmp/new-type-icon-{'rec' if recording else 'idle'}.png"
    img.save(path)
    return path


class MenuBarApp:
    """Thin wrapper so we only import rumps on macOS."""

    def __init__(self, daemon: "Daemon") -> None:
        import rumps
        self._daemon = daemon
        self._icon_idle = _make_tray_icon(recording=False)
        self._icon_rec = _make_tray_icon(recording=True)
        self._status_item = rumps.MenuItem("● Idle")

        class _App(rumps.App):
            pass

        self._app = _App("new-type", icon=self._icon_idle, title=None, quit_button=None)
        self._app.template = True  # adapt to dark/light menu bar (idle icon only)
        self._app.menu = [
            self._status_item,
            None,  # separator
            rumps.MenuItem("Settings…", callback=self._on_settings),
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]

    def set_idle(self) -> None:
        self._app.icon = self._icon_idle
        self._app.template = True
        self._status_item.title = "● Idle"

    def set_recording(self) -> None:
        self._app.icon = self._icon_rec
        self._app.template = False  # keep red color, don't invert
        self._status_item.title = "⏺ Recording…"

    def run(self) -> None:
        self._app.run()

    def stop(self) -> None:
        import rumps
        rumps.quit_application()

    def _on_settings(self, _) -> None:
        self._daemon.config_server.open_browser()

    def _on_quit(self, _) -> None:
        self._daemon.shutdown()


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
        self._transcribe_lock = threading.Lock()
        self._server: socket.socket | None = None

        cc_mode = config.get("chinese_convert")
        if cc_mode:
            from opencc import OpenCC
            self._opencc = OpenCC(cc_mode)
        else:
            self._opencc = None

        self.config_server = ConfigServer()
        self._hotkey = create_hotkey_listener(config, self)

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

        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.005:
            print(f"[new-type] silence skipped (rms={rms:.4f})", flush=True)
            return "silence_skipped"

        if not self._transcribe_lock.acquire(blocking=False):
            print("[transcriber] busy — dropping overlapping transcription request", flush=True)
            return "transcription_busy"

        try:
            ctx = context.collect()
            lang = self.config["transcription"].get("language") or None
            result = self.transcriber.transcribe(audio, language=lang)

            if not result.text:
                return "empty_transcript"

            text = result.text
            # Strip trailing fullwidth digits Whisper hallucinates on short Chinese segments
            # e.g. "狗狗吃牛排１" → "狗狗吃牛排"
            import re
            text = re.sub(r'[\uff00-\uff60]+$', '', text).strip()
            if not text:
                return "empty_transcript"
            if self._opencc:
                text = self._opencc.convert(text)

            cleaned = self.cleanup.clean(text, ctx)

            if not cleaned:
                return "empty_after_cleanup"

            self._inject(cleaned)
            print(f"[new-type] injected: {cleaned[:80]}", flush=True)
            return f"injected:{cleaned[:80]}"
        except Exception as e:
            print(f"[new-type] error: {e}", flush=True)
            return f"error:{e}"
        finally:
            self._transcribe_lock.release()

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

    def handle_test_stop(self) -> str:
        """Stop recording, transcribe, return text — no injection."""
        with self._lock:
            if not self.recorder.is_recording:
                return "(not recording)"
            self.status.set_idle()
        audio = self.recorder.stop()
        if audio is None or len(audio) == 0:
            return "(no audio)"
        duration = len(audio) / self.config["audio"]["sample_rate"]
        if duration < 0.3:
            return "(too short)"
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < 0.01:
            return "(silence)"
        if not self._transcribe_lock.acquire(blocking=False):
            return "(transcriber busy)"
        try:
            lang = self.config["transcription"].get("language") or None
            result = self.transcriber.transcribe(audio, language=lang)
            text = result.text
            import re
            text = re.sub(r'[\uff00-\uff60]+$', '', text).strip()
            if self._opencc and text:
                text = self._opencc.convert(text)
            return text or "(empty)"
        except Exception as e:
            return f"(error: {e})"
        finally:
            self._transcribe_lock.release()

    def handle_status(self) -> str:
        state = "recording" if self.recorder.is_recording else "idle"
        return f"{state} (mode:{self.mode})"

    def shutdown(self) -> None:
        """Clean up and exit. Safe to call from any thread."""
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        Path(self.socket_path).unlink(missing_ok=True)
        self.status.clear()
        sys.exit(0)

    def _run_socket(self) -> None:
        """Socket server loop — runs in a background thread."""
        server = self._server
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
                elif data == "quit":
                    conn.sendall(b"quitting")
                    conn.close()
                    self.shutdown()
                    return
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

    def run(self) -> None:
        sock_path = Path(self.socket_path)
        if sock_path.exists():
            sock_path.unlink()

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(sock_path))
        self._server.listen(5)
        self._server.settimeout(1.0)

        if self._hotkey:
            self._hotkey.start()
            print(f"[new-type] Hotkey active: {self._hotkey.key}", flush=True)

        threading.Thread(target=self._run_socket, daemon=True, name="socket-server").start()
        self.config_server.start(daemon=self)
        print(f"[new-type] Daemon running. Socket: {self.socket_path}", flush=True)

        if sys.platform == "darwin":
            tray = MenuBarApp(self)
            self.status.attach_tray(tray)
            self.status.set_idle()
            tray.run()  # blocks on main thread (NSApp run loop)
        else:
            self.status.start()
            signal.signal(signal.SIGINT, lambda s, f: self.shutdown())
            signal.signal(signal.SIGTERM, lambda s, f: self.shutdown())
            threading.Event().wait()  # block forever; socket thread is daemon


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
