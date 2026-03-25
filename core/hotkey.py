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
        from pynput.keyboard import Key, HotKey
        return HotKey.parse(self.key)

    def _resolve_single_key(self):
        """Return a single Key or KeyCode for single-key bindings like <cmd_r> or <insert>."""
        from pynput.keyboard import Key
        # Strip angle brackets for Key enum lookup: "<cmd_r>" → "cmd_r"
        name = self.key.strip("<>")
        key = getattr(Key, name, None)
        if key is None and not self._is_fn_key():
            print(f"[hotkey] Warning: '{self.key}' not found in pynput Key enum. "
                  f"Run keytest to find the correct name.", flush=True)
        return key

    def _is_single_key(self) -> bool:
        """True if key is a single special key like <fn>, <insert> (no + combos)."""
        return "+" not in self.key

    def _is_fn_key(self) -> bool:
        return self._is_single_key() and self.key.strip("<>") in ("fn", "globe")

    def _run_fn_quartz(self) -> None:
        """
        Fn/Globe via raw Quartz CGEventTap.
        kCGEventFlagsChanged fires with the NEW flag state embedded in the event —
        no polling, no timing issues. Works for both hold and press/toggle modes.
        """
        from Quartz import (
            CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly, CGEventMaskBit, kCGEventFlagsChanged,
            CGEventGetFlags, CGEventGetIntegerValueField, kCGKeyboardEventKeycode,
            kCGEventFlagMaskSecondaryFn, CFMachPortCreateRunLoopSource,
            CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRun,
            kCFRunLoopDefaultMode, CGEventTapEnable,
        )

        FN_VK = 0x3F
        FN_FLAG = kCGEventFlagMaskSecondaryFn
        prev_fn = [False]

        def tap_callback(proxy, event_type, event, refcon):
            vk = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if event_type != kCGEventFlagsChanged or vk != FN_VK:
                return event

            fn_now = bool(CGEventGetFlags(event) & FN_FLAG)
            was = prev_fn[0]
            prev_fn[0] = fn_now

            if fn_now and not was:
                # Physical press
                if self.mode == "hold":
                    if not self._held:
                        self._held = True
                        threading.Thread(target=self.on_start, daemon=True).start()
                else:
                    threading.Thread(target=self.on_start, daemon=True).start()
            elif not fn_now and was:
                # Physical release
                if self.mode == "hold" and self._held:
                    self._held = False
                    threading.Thread(target=self.on_stop, daemon=True).start()

            return event

        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            CGEventMaskBit(kCGEventFlagsChanged),
            tap_callback,
            None,
        )
        if tap is None:
            raise RuntimeError("CGEventTapCreate failed — check Accessibility permission")

        src = CFMachPortCreateRunLoopSource(None, tap, 0)
        loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(loop, src, kCFRunLoopDefaultMode)
        CGEventTapEnable(tap, True)
        print("[hotkey] Fn/Globe tap active (Quartz CGEventTap)", flush=True)
        CFRunLoopRun()

    def _run_fn_press(self) -> None:
        self._run_fn_quartz()

    def _run_fn_hold(self) -> None:
        self._run_fn_quartz()

    def _run_press(self) -> None:
        """For toggle and auto_stop — fires on key press."""
        from pynput import keyboard

        if self._is_fn_key():
            self._run_fn_press()
            return

        if self._is_single_key():
            target = self._resolve_single_key()
            def on_press(key):
                if key == target:
                    threading.Thread(target=self.on_start, daemon=True).start()
            with keyboard.Listener(on_press=on_press) as listener:
                self._listener = listener
                listener.join()
        else:
            target_keys = self._parse_key()
            hotkey = keyboard.HotKey(target_keys, lambda: threading.Thread(target=self.on_start, daemon=True).start())
            with keyboard.Listener(on_press=hotkey.press, on_release=hotkey.release) as listener:
                self._listener = listener
                listener.join()

    def _run_hold(self) -> None:
        """For hold mode — start on press, stop on release."""
        from pynput import keyboard

        if self._is_single_key():
            target = self._resolve_single_key()

            # Fn/Globe special case: pynput bug causes on_press to never fire and
            # on_release to fire twice (once on actual press, once on actual release).
            # Detect which is which using CGEventSourceFlagsState.
            if self._is_fn_key():
                self._run_fn_hold()
                return

            def on_press(key):
                if key == target and not self._held:
                    self._held = True
                    threading.Thread(target=self.on_start, daemon=True).start()
            def on_release(key):
                if key == target and self._held:
                    self._held = False
                    threading.Thread(target=self.on_stop, daemon=True).start()
            with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                self._listener = listener
                listener.join()
            return

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
