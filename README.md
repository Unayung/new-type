# new-type

Local-first voice dictation for Linux and macOS. Speak into any text field — no cloud, no subscription, no data leaving your machine.

Inspired by [Typeless](https://typeless.com), built for privacy, Wayland, and Traditional Chinese.

---

## Features

- **Local Whisper inference** via faster-whisper (large-v3, turbo, and more)
- **GPU acceleration** — CUDA on Linux, CPU on macOS (fast enough with turbo)
- **Three recording modes** — toggle, hold-to-talk, auto-stop on silence
- **Silero VAD** for accurate auto-stop (neural, not just energy threshold)
- **Traditional Chinese output** — OpenCC conversion + initial prompt biasing
- **App-aware context** — reads active window/app to inform LLM cleanup tone
- **LLM cleanup layer** — removes filler words, fixes punctuation (Ollama, OpenAI, Anthropic)
- **Cross-platform hotkey** — built-in via pynput, no window manager needed
- **Waybar integration** — green/red dot indicator, right-click to quit
- **Switchable backends** — Groq, OpenAI, AssemblyAI for cloud transcription

---

## Requirements

### Linux (Wayland / Hyprland)
- Python 3.13+, [uv](https://docs.astral.sh/uv/)
- `wtype` — text injection
- `socat` — fast socket commands from keybinds
- NVIDIA GPU optional (CUDA 12+)

### macOS
- Python 3.13+, [uv](https://docs.astral.sh/uv/)
- `socat` via Homebrew
- Accessibility permission for global hotkey

---

## Installation

```bash
git clone git@github.com:Unayung/new-type.git
cd new-type
uv sync
```

**Linux:**
```bash
sudo pacman -S wtype socat   # Arch/Hyprland
```

**macOS:**
```bash
brew install socat
```

---

## Quick start

```bash
uv run main.py daemon
```

In another terminal:
```bash
uv run main.py toggle   # start/stop recording
uv run main.py devices  # list audio input devices
```

First run downloads the Whisper model (~1.5 GB for turbo, ~3 GB for large-v3).

---

## Configuration

All settings live in `config.yaml`.

### Transcription backend

```yaml
transcription:
  backend: faster_whisper   # faster_whisper | whisper_cpp | groq | openai | assemblyai
  model: turbo              # turbo | large-v3 | small | medium | base | tiny
  device: auto              # auto | cpu | cuda
  compute_type: float16     # float16 (GPU) | int8 (CPU)
  language: null            # null = auto-detect | "zh" | "en" | "ja"
  initial_prompt: null      # bias model output style/script
  hallucination_silence_threshold: 2.0
```

For Traditional Chinese with Taiwan phrasing:
```yaml
  language: zh
  initial_prompt: "以下是台灣繁體中文的日常口語對話，語氣自然隨性，包含中英文夾雜、台灣用語與口語表達。"
```

For cloud (Groq is fastest):
```yaml
  backend: groq
  api_key: your_key_here
  model: whisper-large-v3-turbo
```

### Recording modes

```yaml
recording:
  mode: auto_stop   # toggle | auto_stop | hold
  silence_duration: 0.8
  speech_threshold: 0.5   # Silero VAD probability (0–1)
  min_speech_duration: 0.5
  no_speech_timeout: 5.0
```

| Mode | Behaviour |
|---|---|
| `toggle` | Press once to start, press again to stop |
| `auto_stop` | Press to start, stops automatically after silence |
| `hold` | Hold key to record, release to stop |

### Built-in hotkey (cross-platform)

```yaml
hotkey:
  key: "<insert>"          # Linux example
  # key: "<cmd>+<shift>+a" # macOS example
```

On macOS, grant Accessibility permission when prompted.
On Linux/Wayland, add yourself to the `input` group if needed:
```bash
sudo usermod -aG input $USER   # then re-login
```

### LLM cleanup

```yaml
cleanup:
  backend: none     # none | ollama | openai | anthropic

  # Ollama (local, private):
  # backend: ollama
  # model: llama3.2
```

### Traditional Chinese conversion (OpenCC)

```yaml
chinese_convert: s2twp   # s2t | s2tw | s2twp (Taiwan phrases) | null
```

---

## Linux / Hyprland setup

### Keybinds (`~/.config/hypr/bindings.conf`)

**Hold-to-talk:**
```ini
bind  = , Insert, exec, /path/to/new-type/scripts/nt-start.sh
bindr = , Insert, exec, /path/to/new-type/scripts/nt-stop.sh
```

**Toggle:**
```ini
bindd = , Insert, Dictate, exec, /path/to/new-type/scripts/nt-toggle.sh
```

### Autostart (`~/.config/hypr/autostart.conf`)

```ini
exec-once = /path/to/new-type/scripts/nt-launch.sh
```

### Waybar indicator

Add to `config.jsonc` modules-right:
```json
"custom/new-type"
```

Add module definition:
```json
"custom/new-type": {
  "exec": "/path/to/new-type/scripts/waybar-status.sh",
  "return-type": "json",
  "interval": 1,
  "format": "{}",
  "tooltip": true,
  "on-click-right": "/path/to/new-type/scripts/nt-quit.sh"
}
```

Add to `style.css`:
```css
#custom-new-type { color: #22c55e; margin-right: 15px; }
#custom-new-type.rec { color: #ef4444; }
```

### CUDA on Arch Linux (CUDA 13 + ctranslate2 workaround)

ctranslate2 requires `libcublas.so.12` but Arch ships CUDA 13. The launch script handles this automatically via symlinks in `lib/` and `LD_LIBRARY_PATH`. No extra steps needed.

---

## Commands

```
uv run main.py daemon    Start the background daemon
uv run main.py start     Begin recording
uv run main.py stop      Stop and transcribe
uv run main.py toggle    Start if idle, stop if recording
uv run main.py status    Show current state
uv run main.py devices   List audio input devices
```

Socket commands (via socat, instant — no Python startup):
```bash
echo "start"  | socat - UNIX-CONNECT:/tmp/new-type.sock
echo "stop"   | socat - UNIX-CONNECT:/tmp/new-type.sock
echo "toggle" | socat - UNIX-CONNECT:/tmp/new-type.sock
echo "quit"   | socat - UNIX-CONNECT:/tmp/new-type.sock
```

---

## Architecture

```
new-type/
├── main.py              CLI + daemon (socket IPC, tray status)
├── config.yaml          All settings
├── core/
│   ├── recorder.py      Mic capture (sounddevice) + Silero VAD auto-stop
│   ├── transcriber.py   faster-whisper, whisper.cpp, Groq, OpenAI, AssemblyAI
│   ├── cleanup.py       LLM post-processing (Ollama, OpenAI, Anthropic)
│   ├── context.py       Active app + clipboard context collection
│   └── hotkey.py        Cross-platform global hotkey (pynput)
├── platforms/
│   ├── linux.py         wtype injection, hyprctl context
│   └── macos.py         pbcopy+Cmd+V injection, osascript context
└── scripts/
    ├── nt-launch.sh     Start daemon (idempotent)
    ├── nt-start.sh      Send start via socket
    ├── nt-stop.sh       Send stop via socket
    ├── nt-toggle.sh     Send toggle via socket
    ├── nt-quit.sh       Quit daemon via socket
    ├── run-daemon.sh    Raw daemon start (with LD_LIBRARY_PATH for CUDA)
    └── waybar-status.sh Waybar module status output
```
