"""
Built-in global hotkey listener — cross-platform.

Linux: uses evdev (user must be in 'input' group) — works globally across all
       Wayland and X11 windows without any compositor-specific setup.
macOS: uses pynput (requires Accessibility permission in System Settings).

Supports all three recording modes:
  toggle    — key press calls toggle()
  auto_stop — key press calls start()
  hold      — key press calls start(), key release calls stop()

Key format (pynput notation):
  "<insert>"          — Insert key
  "<ctrl>+'"          — modifier combo
  "<cmd>+<shift>+a"   — macOS Command key
"""

from __future__ import annotations

import sys
import threading
from typing import Callable


# ---------------------------------------------------------------------------
# Key string parser (shared between Linux and macOS paths)
# ---------------------------------------------------------------------------

def _parse_parts(key: str) -> list[str]:
    """Split '<ctrl>+\'' into ['<ctrl>', "'"]."""
    parts = []
    start = 0
    for i, c in enumerate(key):
        if c == "+" and i != start:
            parts.append(key[start:i])
            start = i + 1
    if start == len(key):
        raise ValueError(f"trailing '+' in key: {key!r}")
    parts.append(key[start:])
    return parts


# ---------------------------------------------------------------------------
# Linux evdev hotkey listener
# ---------------------------------------------------------------------------

# Map pynput-style modifier names to pairs of evdev keycodes.
_EVDEV_MODIFIERS: dict[str, tuple[int, int]] = {}

def _build_evdev_modifiers():
    from evdev import ecodes
    return {
        "ctrl":  (ecodes.KEY_LEFTCTRL,  ecodes.KEY_RIGHTCTRL),
        "alt":   (ecodes.KEY_LEFTALT,   ecodes.KEY_RIGHTALT),
        "shift": (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT),
        "super": (ecodes.KEY_LEFTMETA,  ecodes.KEY_RIGHTMETA),
        "cmd":   (ecodes.KEY_LEFTMETA,  ecodes.KEY_RIGHTMETA),
    }


def _char_to_evdev(ch: str) -> int:
    """Map a single character or special-key name to an evdev keycode."""
    from evdev import ecodes

    # Special keys in angle brackets: <insert>, <space>, etc.
    if ch.startswith("<") and ch.endswith(">"):
        name = ch[1:-1].upper()
        attr = f"KEY_{name}"
        code = getattr(ecodes, attr, None)
        if code is None:
            raise ValueError(f"Unknown evdev key: {ch!r}")
        return code

    # Single printable characters — look up by name
    char_map = {
        "'": ecodes.KEY_APOSTROPHE,
        '"': ecodes.KEY_APOSTROPHE,
        "`": ecodes.KEY_GRAVE,
        "~": ecodes.KEY_GRAVE,
        "-": ecodes.KEY_MINUS,
        "_": ecodes.KEY_MINUS,
        "=": ecodes.KEY_EQUAL,
        "+": ecodes.KEY_EQUAL,
        "[": ecodes.KEY_LEFTBRACE,
        "{": ecodes.KEY_LEFTBRACE,
        "]": ecodes.KEY_RIGHTBRACE,
        "}": ecodes.KEY_RIGHTBRACE,
        "\\": ecodes.KEY_BACKSLASH,
        "|": ecodes.KEY_BACKSLASH,
        ";": ecodes.KEY_SEMICOLON,
        ":": ecodes.KEY_SEMICOLON,
        ",": ecodes.KEY_COMMA,
        "<": ecodes.KEY_COMMA,
        ".": ecodes.KEY_DOT,
        ">": ecodes.KEY_DOT,
        "/": ecodes.KEY_SLASH,
        "?": ecodes.KEY_SLASH,
        " ": ecodes.KEY_SPACE,
    }
    if ch in char_map:
        return char_map[ch]

    if ch.isalpha():
        code = getattr(ecodes, f"KEY_{ch.upper()}", None)
        if code is not None:
            return code

    if ch.isdigit():
        code = getattr(ecodes, f"KEY_{ch}", None)
        if code is not None:
            return code

    raise ValueError(f"Cannot map character to evdev keycode: {ch!r}")


def _parse_evdev_combo(key: str):
    """
    Parse a pynput-format key string into (modifier_codes, trigger_code).

    modifier_codes: frozenset of evdev keycodes that act as modifiers
    trigger_code:   single evdev keycode for the non-modifier key
    """
    from evdev import ecodes
    modifiers = _build_evdev_modifiers()

    parts = _parse_parts(key)
    mod_codes: set[int] = set()
    trigger: int | None = None

    for part in parts:
        part_lower = part.strip("<>").lower() if part.startswith("<") else part
        if part.startswith("<") and part.endswith(">") and part_lower in modifiers:
            mod_codes.update(modifiers[part_lower])
        else:
            if trigger is not None:
                raise ValueError(f"Multiple non-modifier keys in combo: {key!r}")
            trigger = _char_to_evdev(part)

    if trigger is None:
        raise ValueError(f"No trigger key found in: {key!r}")

    return frozenset(mod_codes), trigger


class EvdevHotkeyListener:
    """
    Global hotkey listener for Linux using evdev.

    Reads raw input events directly from /dev/input/event* devices.
    Requires the user to be in the 'input' group.
    """

    def __init__(
        self,
        key: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
    ):
        self.key = key
        self.mode = mode
        self.on_start = on_start
        self.on_stop = on_stop
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._held = False
        self._lock = threading.Lock()

        self._mod_codes, self._trigger = _parse_evdev_combo(key)
        # All codes that matter for tracking
        self._watched = self._mod_codes | {self._trigger}
        # Per-thread pressed sets shared via a shared state dict
        self._pressed: set[int] = set()
        self._pressed_lock = threading.Lock()

    def start(self) -> None:
        import evdev
        from evdev import ecodes

        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
                caps = dev.capabilities()
                if ecodes.EV_KEY in caps:
                    devices.append(dev)
            except Exception:
                pass

        if not devices:
            print("[hotkey/evdev] No keyboard devices found — hotkey disabled.", flush=True)
            return

        for dev in devices:
            t = threading.Thread(
                target=self._listen_device,
                args=(dev,),
                daemon=True,
                name=f"hotkey-evdev-{dev.name[:20]}",
            )
            self._threads.append(t)
            t.start()

        print(f"[hotkey/evdev] Listening on {len(devices)} device(s) for {self.key!r}", flush=True)

    def _listen_device(self, dev) -> None:
        import evdev
        try:
            for event in dev.read_loop():
                if self._stop_event.is_set():
                    break
                if event.type != evdev.ecodes.EV_KEY:
                    continue
                code = event.code
                value = event.value  # 0=up, 1=down, 2=hold
                if code not in self._watched:
                    continue
                self._handle_key(code, value)
        except OSError:
            pass
        except Exception as e:
            print(f"[hotkey/evdev] Device error ({dev.name}): {e}", flush=True)

    def _handle_key(self, code: int, value: int) -> None:
        """value: 0=release, 1=press, 2=repeat"""
        with self._pressed_lock:
            if value == 1:
                self._pressed.add(code)
            elif value == 0:
                self._pressed.discard(code)
            else:
                return  # ignore repeat events

            trigger_down = self._trigger in self._pressed
            mods_satisfied = bool(self._mod_codes) and self._mod_codes.intersection(self._pressed) or not self._mod_codes

            combo_active = trigger_down and mods_satisfied

        if self.mode == "hold":
            if combo_active and not self._held:
                with self._lock:
                    if not self._held:
                        self._held = True
                        threading.Thread(target=self.on_start, daemon=True).start()
            elif not combo_active and self._held:
                with self._lock:
                    if self._held:
                        self._held = False
                        threading.Thread(target=self.on_stop, daemon=True).start()
        elif self.mode == "toggle":
            if combo_active and value == 1:
                threading.Thread(target=self.on_start, daemon=True).start()
        elif self.mode == "auto_stop":
            if combo_active and value == 1:
                threading.Thread(target=self.on_start, daemon=True).start()

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# macOS / pynput hotkey listener (unchanged from before)
# ---------------------------------------------------------------------------

class HotkeyListener:
    def __init__(
        self,
        key: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
    ):
        self.key = key
        self.mode = mode
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
        name = self.key.strip("<>")
        key = getattr(Key, name, None)
        if key is None and not self._is_fn_key():
            print(f"[hotkey] Warning: '{self.key}' not found in pynput Key enum. "
                  f"Run keytest to find the correct name.", flush=True)
        return key

    def _is_single_key(self) -> bool:
        return "+" not in self.key

    def _is_fn_key(self) -> bool:
        return self._is_single_key() and self.key.strip("<>") in ("fn", "globe")

    def _run_fn_quartz(self) -> None:
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
                if self.mode == "hold":
                    if not self._held:
                        self._held = True
                        threading.Thread(target=self.on_start, daemon=True).start()
                else:
                    threading.Thread(target=self.on_start, daemon=True).start()
            elif not fn_now and was:
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
        from pynput import keyboard

        if self._is_single_key():
            target = self._resolve_single_key()

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


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_hotkey_listener(config: dict, daemon) -> EvdevHotkeyListener | HotkeyListener | None:
    """
    Create a hotkey listener from config.
    On Linux: uses EvdevHotkeyListener (evdev, global across all windows).
    On macOS: uses HotkeyListener (pynput/Quartz).
    Returns None if no hotkey configured.
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

    if sys.platform == "linux":
        return EvdevHotkeyListener(key=key, mode=mode, on_start=on_start, on_stop=on_stop)
    else:
        return HotkeyListener(key=key, mode=mode, on_start=on_start, on_stop=on_stop)
