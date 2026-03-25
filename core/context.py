"""
Context collector — gathers foreground app info and clipboard.
Platform-specific calls are delegated to the platform layer.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field


@dataclass
class AppContext:
    app_class: str = ""       # e.g. "slack", "firefox", "kitty"
    window_title: str = ""    # e.g. "general | My Workspace — Slack"
    clipboard: str = ""       # current clipboard text
    url: str = ""             # browser URL if detectable from title

    def to_prompt_fragment(self) -> str:
        """Human-readable context string for LLM prompt."""
        parts = []
        if self.app_class:
            parts.append(f"App: {self.app_class}")
        if self.window_title:
            parts.append(f"Window: {self.window_title}")
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.clipboard:
            snippet = self.clipboard[:200].replace("\n", " ")
            parts.append(f"Clipboard: {snippet}")
        return "\n".join(parts)


def collect() -> AppContext:
    """Collect context for the currently focused window."""
    if sys.platform == "darwin":
        from platforms.macos import get_context
    else:
        from platforms.linux import get_context
    return get_context()
