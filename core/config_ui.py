"""
Local web config UI — served on localhost for the Settings menu item.

Opens a browser page where users can edit key config values without
touching config.yaml directly. Changes are applied immediately via
hot-reload — no daemon restart needed.
"""

from __future__ import annotations

import html as _html_mod
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>new-type settings</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
    background: #f5f5f7;
    color: #1d1d1f;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    padding: 40px 16px;
  }
  .card {
    background: #fff;
    border-radius: 12px;
    box-shadow: 0 2px 16px rgba(0,0,0,.08);
    width: 100%;
    max-width: 480px;
    padding: 32px;
    height: fit-content;
  }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 24px; }
  h2 { font-size: 12px; font-weight: 600; color: #6e6e73; text-transform: uppercase;
       letter-spacing: .05em; margin: 24px 0 12px; }
  h2:first-of-type { margin-top: 0; }
  .row { margin-bottom: 14px; }
  label { display: block; font-size: 13px; color: #3a3a3c; margin-bottom: 4px; }
  input[type=text], select {
    width: 100%;
    padding: 8px 10px;
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    font-size: 14px;
    outline: none;
    transition: border-color .15s;
  }
  input[type=text]:focus, select:focus { border-color: #0071e3; }
  .hotkey-row { display: flex; gap: 8px; }
  .hotkey-row input { flex: 1; font-family: monospace; }
  button.capture {
    padding: 8px 12px;
    background: #f5f5f7;
    border: 1px solid #d2d2d7;
    border-radius: 8px;
    font-size: 13px;
    cursor: pointer;
    white-space: nowrap;
    transition: background .15s;
  }
  button.capture:hover { background: #e8e8ed; }
  button.capture.listening {
    background: #fff3cd;
    border-color: #f0ad4e;
    color: #856404;
  }
  .hint { font-size: 11px; color: #8e8e93; margin-top: 3px; }
  .actions { margin-top: 28px; display: flex; gap: 10px; justify-content: flex-end; }
  button.save {
    padding: 9px 20px;
    background: #0071e3;
    color: #fff;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: background .15s;
  }
  button.save:hover { background: #0077ed; }
  .toast {
    display: none;
    margin-top: 16px;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    text-align: center;
  }
  .toast.ok  { background: #d1f2e0; color: #1a7a3c; display: block; }
  .toast.err { background: #ffe0e0; color: #8b1a1a; display: block; }
  hr { border: none; border-top: 1px solid #f0f0f5; margin: 4px 0 8px; }
</style>
</head>
<body>
<div class="card">
  <h1>new-type settings</h1>

  <h2>Hotkey</h2>
  <div class="row">
    <label>Key binding</label>
    <div class="hotkey-row">
      <input type="text" id="hotkey_key" value="__hotkey_key__" placeholder="&lt;fn&gt; or &lt;cmd_r&gt;">
      <button class="capture" id="captureBtn" onclick="startCapture()">Capture</button>
    </div>
    <div class="hint">pynput format: &lt;fn&gt; · &lt;cmd_r&gt; · &lt;ctrl&gt;+&lt;alt&gt;+a</div>
  </div>

  <h2>Recording</h2>
  <div class="row">
    <label>Mode</label>
    <select id="recording_mode">
      <option value="hold" __sel_hold__>Hold — hold key to record, release to transcribe</option>
      <option value="toggle" __sel_toggle__>Toggle — press once to start, again to stop</option>
      <option value="auto_stop" __sel_auto__>Auto-stop — stops after silence</option>
    </select>
  </div>

  <h2>Transcription</h2>
  <div class="row">
    <label>Model</label>
    <select id="transcription_model">
      <option value="large-v3-turbo" __sel_lv3t__>large-v3-turbo (best, ~800 MB)</option>
      <option value="large-v3" __sel_lv3__>large-v3 (~3 GB)</option>
      <option value="turbo" __sel_turbo__>turbo (fast, ~1.5 GB)</option>
      <option value="medium" __sel_med__>medium (~1.5 GB)</option>
      <option value="small" __sel_sm__>small (~500 MB)</option>
      <option value="base" __sel_base__>base (~150 MB)</option>
      <option value="tiny" __sel_tiny__>tiny (~80 MB)</option>
    </select>
  </div>
  <div class="row">
    <label>Language</label>
    <input type="text" id="transcription_language" value="__transcription_language__" placeholder="zh · en · ja · (blank = auto-detect)">
    <div class="hint">ISO 639-1 code, or leave blank for auto-detect</div>
  </div>
  <div class="row">
    <label>Initial prompt</label>
    <textarea id="transcription_initial_prompt" rows="3"
      style="width:100%;padding:8px 10px;border:1px solid #d2d2d7;border-radius:8px;
             font-size:13px;resize:vertical;color:#3a3a3c;background:#fafafa;transition:border-color .15s;"
      onfocus="this.style.borderColor='#0071e3'" onblur="this.style.borderColor='#d2d2d7'"
      placeholder="e.g. 以下是台灣繁體中文的日常口語對話…">__transcription_initial_prompt__</textarea>
    <div class="hint">Biases Whisper toward a vocabulary/style — acts as a "prior transcript" prefix</div>
  </div>

  <h2>Chinese conversion</h2>
  <div class="row">
    <label>OpenCC mode</label>
    <select id="chinese_convert">
      <option value="" __sel_cc_none__>(disabled)</option>
      <option value="s2t" __sel_cc_s2t__>s2t — Simplified → Traditional</option>
      <option value="s2tw" __sel_cc_s2tw__>s2tw — Simplified → Taiwan</option>
      <option value="s2twp" __sel_cc_s2twp__>s2twp — Simplified → Taiwan + phrases (recommended)</option>
    </select>
  </div>

  <h2>Test transcription</h2>
  <div class="row">
    <button class="capture" id="testBtn"
      onpointerdown="testStart(event)" onpointerup="testStop(event)" onpointerleave="testStop(event)"
      style="width:100%;padding:12px;font-size:14px;">Hold to speak</button>
    <textarea id="testOut" readonly rows="3"
      style="width:100%;margin-top:8px;padding:8px 10px;border:1px solid #d2d2d7;border-radius:8px;
             font-size:14px;resize:vertical;color:#3a3a3c;background:#fafafa;"></textarea>
    <div class="hint">Transcribes using current model settings — text is not injected</div>
  </div>

  <div class="actions">
    <button class="save" onclick="save()">Save</button>
  </div>
  <div class="toast" id="toast"></div>
</div>

<script>
// Map browser e.key → pynput format
const KEY_MAP = {
  'Insert': '<insert>', 'Delete': '<delete>', 'Home': '<home>', 'End': '<end>',
  'PageUp': '<page_up>', 'PageDown': '<page_down>',
  'ArrowUp': '<up>', 'ArrowDown': '<down>', 'ArrowLeft': '<left>', 'ArrowRight': '<right>',
  'Escape': '<esc>', 'Tab': '<tab>', 'CapsLock': '<caps_lock>',
  'F1':'<f1>','F2':'<f2>','F3':'<f3>','F4':'<f4>','F5':'<f5>','F6':'<f6>',
  'F7':'<f7>','F8':'<f8>','F9':'<f9>','F10':'<f10>','F11':'<f11>','F12':'<f12>',
  'PrintScreen':'<print_screen>','ScrollLock':'<scroll_lock>','Pause':'<pause>',
  'NumLock':'<num_lock>',
  'Control': '<ctrl>', 'Alt': '<alt>', 'Shift': '<shift>',
  'Meta': '<cmd>', 'ContextMenu': '<menu>',
};

function startCapture() {
  const btn = document.getElementById('captureBtn');
  const inp = document.getElementById('hotkey_key');
  btn.classList.add('listening');
  btn.textContent = 'Press a key…';
  inp.value = '';

  let done = false;

  function finish(key) {
    if (done) return;
    done = true;
    inp.value = key;
    btn.classList.remove('listening');
    btn.textContent = 'Capture';
    document.removeEventListener('keydown', onKey, true);
  }

  // Browser-side capture (works for Insert and most keys on Linux/Wayland)
  function onKey(e) {
    e.preventDefault();
    e.stopPropagation();
    const mapped = KEY_MAP[e.key];
    if (mapped) {
      finish(mapped);
    } else if (e.key.length === 1) {
      // Regular printable character
      finish(`<${e.key}>`);
    }
    // Ignore unrecognized keys (e.g. lone modifier with no mapping)
  }
  document.addEventListener('keydown', onKey, true);

  // Also ask daemon for Fn/Globe key (macOS — not visible to browser)
  fetch('/capture/start', { method: 'POST' }).then(() => {
    const poll = setInterval(() => {
      if (done) { clearInterval(poll); return; }
      fetch('/capture/result')
        .then(r => r.json())
        .then(r => { if (r.key) { clearInterval(poll); finish(r.key); } });
    }, 150);
    setTimeout(() => { clearInterval(poll); if (!done) { btn.classList.remove('listening'); btn.textContent = 'Capture'; document.removeEventListener('keydown', onKey, true); } }, 10000);
  });
}

let _testing = false;
function testStart(e) {
  if (_testing) return;
  _testing = true;
  e.currentTarget.classList.add('listening');
  e.currentTarget.textContent = 'Recording…';
  document.getElementById('testOut').value = '';
  fetch('/test/start', { method: 'POST' });
}
function testStop(e) {
  if (!_testing) return;
  _testing = false;
  const btn = e.currentTarget;
  btn.textContent = 'Transcribing…';
  fetch('/test/stop', { method: 'POST' })
    .then(r => r.json())
    .then(r => {
      btn.classList.remove('listening');
      btn.textContent = 'Hold to speak';
      document.getElementById('testOut').value = r.text || r.error || '(nothing)';
    });
}

function save() {
  const data = {
    hotkey_key: document.getElementById('hotkey_key').value.trim(),
    recording_mode: document.getElementById('recording_mode').value,
    transcription_model: document.getElementById('transcription_model').value,
    transcription_language: document.getElementById('transcription_language').value.trim(),
    transcription_initial_prompt: document.getElementById('transcription_initial_prompt').value,
    chinese_convert: document.getElementById('chinese_convert').value,
  };
  fetch('/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data),
  })
  .then(r => r.json())
  .then(r => {
    const t = document.getElementById('toast');
    if (r.ok) {
      t.className = 'toast ok';
      t.textContent = '✓ Saved';
      setTimeout(() => location.reload(), 800);
    } else {
      t.className = 'toast err';
      t.textContent = '✗ Error: ' + r.error;
    }
  });
}
</script>
</body>
</html>
"""


def _sel(condition: bool) -> str:
    return "selected" if condition else ""


def _render(config: dict) -> str:
    hk = config.get("hotkey", {}).get("key") or ""
    mode = config.get("recording", {}).get("mode", "hold")
    model = config.get("transcription", {}).get("model", "large-v3-turbo")
    lang = config.get("transcription", {}).get("language", "") or ""
    initial_prompt = config.get("transcription", {}).get("initial_prompt", "") or ""
    cc = config.get("chinese_convert", "") or ""

    replacements = {
        "__hotkey_key__": hk,
        "__sel_hold__": _sel(mode == "hold"),
        "__sel_toggle__": _sel(mode == "toggle"),
        "__sel_auto__": _sel(mode == "auto_stop"),
        "__sel_lv3t__": _sel(model == "large-v3-turbo"),
        "__sel_lv3__": _sel(model == "large-v3"),
        "__sel_turbo__": _sel(model == "turbo"),
        "__sel_med__": _sel(model == "medium"),
        "__sel_sm__": _sel(model == "small"),
        "__sel_base__": _sel(model == "base"),
        "__sel_tiny__": _sel(model == "tiny"),
        "__transcription_language__": lang,
        "__transcription_initial_prompt__": _html_mod.escape(initial_prompt),
        "__sel_cc_none__": _sel(cc == ""),
        "__sel_cc_s2t__": _sel(cc == "s2t"),
        "__sel_cc_s2tw__": _sel(cc == "s2tw"),
        "__sel_cc_s2twp__": _sel(cc == "s2twp"),
    }
    html = _HTML
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)
    return html


def _apply(config: dict, data: dict) -> dict:
    config.setdefault("hotkey", {})["key"] = data.get("hotkey_key", "")
    config.setdefault("recording", {})["mode"] = data.get("recording_mode", "hold")
    config.setdefault("transcription", {})["model"] = data.get("transcription_model", "large-v3-turbo")
    lang = data.get("transcription_language", "").strip() or None
    config["transcription"]["language"] = lang
    config["transcription"]["initial_prompt"] = data.get("transcription_initial_prompt", "").strip() or None
    cc = data.get("chinese_convert", "").strip() or None
    config["chinese_convert"] = cc
    return config


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _KeyCapture:
    """
    One-shot key listener using pynput + Quartz (for Fn/Globe).
    Both run in parallel; whichever fires first wins.
    """

    def __init__(self) -> None:
        self._result: str | None = None
        self._done = threading.Event()

    def start(self) -> None:
        self._result = None
        self._done.clear()
        threading.Thread(target=self._listen_pynput, daemon=True).start()
        threading.Thread(target=self._listen_fn_quartz, daemon=True).start()

    def result(self) -> str | None:
        return self._result

    def _set(self, key: str) -> None:
        if not self._done.is_set():
            self._result = key
            self._done.set()

    def _listen_pynput(self) -> None:
        try:
            from pynput import keyboard

            MODIFIER_KEYS = {
                keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
                keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
                keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
                keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
            }
            MODIFIER_NAME = {
                keyboard.Key.cmd: 'cmd', keyboard.Key.cmd_l: 'cmd', keyboard.Key.cmd_r: 'cmd',
                keyboard.Key.alt: 'alt', keyboard.Key.alt_l: 'alt', keyboard.Key.alt_r: 'alt',
                keyboard.Key.shift: 'shift', keyboard.Key.shift_l: 'shift', keyboard.Key.shift_r: 'shift',
                keyboard.Key.ctrl: 'ctrl', keyboard.Key.ctrl_l: 'ctrl', keyboard.Key.ctrl_r: 'ctrl',
            }
            # macOS virtual key code → base character (US QWERTY, unaffected by modifiers)
            VK_TO_CHAR = {
                0:'a',1:'s',2:'d',3:'f',4:'h',5:'g',6:'z',7:'x',8:'c',9:'v',
                11:'b',12:'q',13:'w',14:'e',15:'r',16:'y',17:'t',18:'1',19:'2',
                20:'3',21:'4',22:'6',23:'5',24:'=',25:'9',26:'7',27:'-',28:'8',
                29:'0',30:']',31:'o',32:'u',33:'[',34:'i',35:'p',37:'l',38:'j',
                39:"'",40:'k',41:';',42:'\\',43:',',44:'/',45:'n',46:'m',47:'.',
                50:'`',
            }
            held_mods: set = set()

            def on_press(key):
                if self._done.is_set():
                    return False
                if key in MODIFIER_KEYS:
                    held_mods.add(MODIFIER_NAME[key])
                    return  # keep listening for the trigger key
                # Non-modifier: use vk to get the physical key name (ignores Option composition)
                vk = getattr(key, 'vk', None)
                if vk is not None and vk in VK_TO_CHAR:
                    key_str = VK_TO_CHAR[vk]
                else:
                    try:
                        key_str = f"<{key.name}>"
                    except AttributeError:
                        char = getattr(key, 'char', None)
                        if char and char.isprintable():
                            key_str = char.lower()
                        else:
                            return  # ignore, keep listening

                if held_mods:
                    mod_order = ['cmd', 'ctrl', 'shift', 'alt']
                    mods = [m for m in mod_order if m in held_mods]
                    self._set('+'.join(f"<{m}>" for m in mods) + '+' + key_str)
                else:
                    self._set(key_str if key_str.startswith('<') else f"<{key_str}>")
                return False

            with keyboard.Listener(on_press=on_press) as lst:
                self._done.wait(timeout=10)
                lst.stop()
        except Exception:
            pass

    def _listen_fn_quartz(self) -> None:
        try:
            import sys as _sys
            if _sys.platform != "darwin":
                return
            from Quartz import (
                CGEventTapCreate, kCGSessionEventTap, kCGHeadInsertEventTap,
                kCGEventTapOptionListenOnly, CGEventMaskBit, kCGEventFlagsChanged,
                CGEventGetFlags, CGEventGetIntegerValueField, kCGKeyboardEventKeycode,
                kCGEventFlagMaskSecondaryFn, CFMachPortCreateRunLoopSource,
                CFRunLoopGetCurrent, CFRunLoopAddSource, CFRunLoopRunInMode,
                kCFRunLoopDefaultMode, CGEventTapEnable,
            )

            FN_VK = 0x3F
            FN_FLAG = kCGEventFlagMaskSecondaryFn
            prev = [False]

            def cb(proxy, event_type, event, _refcon):
                vk = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                if event_type == kCGEventFlagsChanged and vk == FN_VK:
                    fn_now = bool(CGEventGetFlags(event) & FN_FLAG)
                    if fn_now and not prev[0]:
                        self._set("<fn>")
                    prev[0] = fn_now
                return event

            tap = CGEventTapCreate(
                kCGSessionEventTap, kCGHeadInsertEventTap,
                kCGEventTapOptionListenOnly,
                CGEventMaskBit(kCGEventFlagsChanged), cb, None,
            )
            if tap is None:
                return
            src = CFMachPortCreateRunLoopSource(None, tap, 0)
            loop = CFRunLoopGetCurrent()
            CFRunLoopAddSource(loop, src, kCFRunLoopDefaultMode)
            CGEventTapEnable(tap, True)
            # Spin until done or timeout
            deadline = 10.0
            while not self._done.is_set() and deadline > 0:
                CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, False)
                deadline -= 0.1
        except Exception:
            pass


# Singleton capture session
_capture = _KeyCapture()


def _apply_and_reload(config: dict) -> None:
    """Hot-reload daemon settings after a short delay (lets HTTP response flush)."""
    import time
    time.sleep(0.1)
    if _daemon:
        _daemon.apply_new_config(config)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress access log

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            config = yaml.safe_load(CONFIG_PATH.read_text())
            body = _render(config).encode()
            self._json_or_html(body, "text/html; charset=utf-8")
        elif path == "/capture/result":
            body = json.dumps({"key": _capture.result()}).encode()
            self._json_or_html(body, "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def _json_or_html(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/capture/start":
            _capture.start()
            self.send_response(204)
            self.end_headers()
            return
        if path == "/test/start":
            if _daemon:
                _daemon.handle_start()
            self.send_response(204)
            self.end_headers()
            return
        if path == "/test/stop":
            text = _daemon.handle_test_stop() if _daemon else "(no daemon)"
            body = json.dumps({"text": text}).encode()
            self._json_or_html(body, "application/json")
            return
        if path != "/save":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        new_config = None
        try:
            data = json.loads(raw)
            config = yaml.safe_load(CONFIG_PATH.read_text())
            config = _apply(config, data)
            CONFIG_PATH.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False))
            new_config = config
            resp = json.dumps({"ok": True}).encode()
        except Exception as e:
            resp = json.dumps({"ok": False, "error": str(e)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)
        if new_config is not None:
            threading.Thread(target=_apply_and_reload, args=(new_config,), daemon=True).start()


_daemon = None  # set by ConfigServer.start()


class ConfigServer:
    """Tiny localhost HTTP server for the settings UI."""

    def __init__(self, port: int = 47821):
        self._port = port
        self._server: HTTPServer | None = None

    def start(self, daemon=None) -> None:
        global _daemon
        _daemon = daemon
        try:
            self._server = HTTPServer(("127.0.0.1", self._port), _Handler)
        except OSError:
            print(f"[config-ui] Port {self._port} already in use — settings UI unavailable", flush=True)
            return
        t = threading.Thread(target=self._server.serve_forever, daemon=True, name="config-ui")
        t.start()
        print(f"[config-ui] Settings UI at http://127.0.0.1:{self._port}/", flush=True)

    def open_browser(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self._port}/")
