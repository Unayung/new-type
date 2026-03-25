# new-type setup

## 1. Start the daemon (auto-start with Hyprland)

Add to `~/.config/hypr/hyprland.conf`:

```ini
# new-type — start daemon on login
exec-once = /home/unayung/Projects/new-type/scripts/run-daemon.sh
```

Create `scripts/run-daemon.sh`:
```bash
#!/bin/bash
cd /home/unayung/Projects/new-type
uv run main.py daemon
```

## 2. Keybind — hold to record

Add to `~/.config/hypr/hyprland.conf`:

```ini
# Hold Super+D to record, release to transcribe and inject
bind = SUPER, D, exec, uv --directory /home/unayung/Projects/new-type run main.py start
bindr = SUPER, D, exec, uv --directory /home/unayung/Projects/new-type run main.py stop
```

> `bind` fires on key press, `bindr` fires on key release.
> Hold the key while speaking, release to transcribe.

Or use **toggle** mode (press once to start, press again to stop):

```ini
bind = SUPER, D, exec, uv --directory /home/unayung/Projects/new-type run main.py toggle
```

## 3. First run — download Whisper model

On first use, faster-whisper will download the model (~1.5 GB for turbo):

```bash
cd ~/Projects/new-type
uv run main.py daemon
```

Models are cached in `~/.cache/huggingface/hub/`.

## 4. Optional: Belle Turbo (Chinese/English optimized)

Install whisper.cpp:
```bash
yay -S whisper.cpp
```

Download Belle Turbo GGUF model and put it in:
```
~/.local/share/new-type/models/belle-turbo.gguf
```

Then in `config.yaml`:
```yaml
transcription:
  backend: whisper_cpp
  model_path: ~/.local/share/new-type/models/belle-turbo.gguf
```

## 5. Optional: LLM cleanup with Ollama

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
```

Then in `config.yaml`:
```yaml
cleanup:
  backend: ollama
  model: llama3.2
```

## 6. Tray icon

The daemon shows a dot in your Waybar tray:
- **Green** = idle, ready to record
- **Red** = recording in progress

Make sure Waybar has the tray module enabled in your config.
