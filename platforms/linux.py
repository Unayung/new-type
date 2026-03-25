"""
Linux/Wayland platform layer (Hyprland).

- Text injection: wtype
- Context: hyprctl activewindow
- Clipboard: wl-paste
"""

from __future__ import annotations

import json
import subprocess

from core.context import AppContext


def inject_text(text: str) -> None:
    """Type text into the focused window via wtype."""
    subprocess.run(["wtype", text], check=True)


def get_context() -> AppContext:
    ctx = AppContext()

    # Active window via hyprctl
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            win = json.loads(result.stdout)
            ctx.app_class = win.get("class", "").lower()
            ctx.window_title = win.get("title", "")
            # Extract URL from browser titles like "Claude - Mozilla Firefox"
            ctx.url = _extract_url_from_title(ctx.app_class, ctx.window_title)
    except Exception:
        pass

    # Clipboard via wl-paste
    try:
        result = subprocess.run(
            ["wl-paste", "--no-newline"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            ctx.clipboard = result.stdout
    except Exception:
        pass

    return ctx


def _extract_url_from_title(app_class: str, title: str) -> str:
    """
    Browser window titles often contain the page title but not the URL.
    We can only extract a URL if it appears directly in the title (rare).
    """
    browsers = {"firefox", "chromium", "chrome", "brave", "vivaldi", "opera"}
    if app_class in browsers:
        # Some browsers show the URL in the title when in address bar
        for part in title.split(" — "):
            part = part.strip()
            if part.startswith(("http://", "https://")):
                return part
    return ""
