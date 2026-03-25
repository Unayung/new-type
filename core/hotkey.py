"""
Built-in global hotkey listener — cross-platform via pynput.

Supports all three recording modes:
  toggle    — key press calls toggle()
  auto_stop — key press calls start()
  hold      — key press calls start(), key release calls stop()

Key format (pynput notation):
  "<insert>"          — Insert key
  "<ctrl>+<alt>+a"    — modifier combo
  "<cmd>+<shift>+a"   — macOS Command key

On macOS: requires Accessibility permission (System Settings → Privacy → Accessibility).
On Linux/Wayland: works via evdev (user must be in 'input' group) or XWayland.
           If it fails, daemon continues without hotkey — use Hyprland keybinds instead.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable


class HotkeyListener:
    def __init__(
        self,
        key: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
    ):
        self.key = key
        self.mode = mode  # toggle | auto_stop | hold
        self.on_start = on_start
        self.on_stop = on_stop
        self._thread: threading.Thread | None = None
        self._listener = None
        self._held = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            if self.mode == "hold":
                self._run_hold()
            else:
                self._run_press()
        except Exception as e:
            print(f"[hotkey] Failed to start: {e}", flush=True)
            print("[hotkey] Continuing without built-in hotkey — use system keybinds instead.", flush=True)

    def _parse_key(self):
        from pynput.keyboard import Key, KeyCode, HotKey
        # Parse key string like "<insert>", "<ctrl>+<alt>+a"
        return HotKey.parse(self.key)

    def _run_press(self) -> None:
        """For toggle and auto_stop — fires on key press."""
        from pynput import keyboard

        target_keys = self._parse_key()
        cb = self.on_start if self.mode == "auto_stop" else None

        def on_activate():
            if self.mode == "toggle":
                # toggle is handled by the daemon
                self.on_start()  # on_start is bound to handle_toggle in this mode
            else:
                self.on_start()

        hotkey = keyboard.HotKey(target_keys, on_activate)

        with keyboard.Listener(
            on_press=hotkey.press,
            on_release=hotkey.release,
        ) as listener:
            self._listener = listener
            listener.join()

    def _run_hold(self) -> None:
        """For hold mode — start on press, stop on release."""
        from pynput import keyboard

        target_keys = set(self._parse_key())
        currently_pressed: set = set()

        def canonical(key):
            try:
                return listener.canonical(key)
            except Exception:
                return key

        def on_press(key):
            k = canonical(key)
            currently_pressed.add(k)
            if target_keys.issubset(currently_pressed) and not self._held:
                self._held = True
                threading.Thread(target=self.on_start, daemon=True).start()

        def on_release(key):
            k = canonical(key)
            currently_pressed.discard(k)
            if self._held and not target_keys.issubset(currently_pressed):
                self._held = False
                threading.Thread(target=self.on_stop, daemon=True).start()

        with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
            self._listener = listener
            listener.join()


def create_hotkey_listener(config: dict, daemon) -> HotkeyListener | None:
    """
    Create a HotkeyListener from config if hotkey is configured.
    Returns None if no hotkey configured.

    daemon must have: handle_start(), handle_stop(), handle_toggle() methods.
    """
    hotkey_cfg = config.get("hotkey", {})
    if not hotkey_cfg or not hotkey_cfg.get("key"):
        return None

    key = hotkey_cfg["key"]
    mode = config.get("recording", {}).get("mode", "toggle")

    if mode == "toggle":
        on_start = lambda: daemon.handle_toggle()
        on_stop = lambda: None
    elif mode == "auto_stop":
        on_start = lambda: daemon.handle_start()
        on_stop = lambda: None
    elif mode == "hold":
        on_start = lambda: daemon.handle_start()
        on_stop = lambda: daemon.handle_stop()
    else:
        return None

    return HotkeyListener(key=key, mode=mode, on_start=on_start, on_stop=on_stop)
