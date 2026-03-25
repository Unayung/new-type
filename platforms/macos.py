"""
macOS platform layer.

- Text injection: pbcopy + Cmd+V (most reliable across apps)
- Context: osascript
- Clipboard: pbpaste
"""

from __future__ import annotations

import subprocess

from core.context import AppContext


def inject_text(text: str) -> None:
    """
    Inject text by writing to clipboard then simulating Cmd+V.
    More reliable than pyautogui.typewrite for non-ASCII / Chinese text.
    Saves and restores the original clipboard content.
    """
    import time

    # Save current clipboard
    original = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout

    # Write new text to clipboard
    subprocess.run(["pbcopy"], input=text, text=True, check=True)
    time.sleep(0.05)

    # Simulate Cmd+V
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down',
    ], check=True)
    time.sleep(0.1)

    # Restore original clipboard
    subprocess.run(["pbcopy"], input=original, text=True)


def get_context() -> AppContext:
    ctx = AppContext()

    # Frontmost app name and window title via osascript
    script = """
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
        set frontAppPath to bundle identifier of first application process whose frontmost is true
    end tell
    set winTitle to ""
    try
        tell application frontApp
            set winTitle to name of front window
        end tell
    end try
    return frontApp & "|" & winTitle
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 1)
            ctx.app_class = parts[0].lower().replace(" ", "-") if parts else ""
            ctx.window_title = parts[1] if len(parts) > 1 else ""
    except Exception:
        pass

    # Clipboard
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            ctx.clipboard = result.stdout
    except Exception:
        pass

    return ctx
