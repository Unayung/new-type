"""
LLM cleanup layer — post-processes raw Whisper transcript.

Takes raw text + app context, returns polished text:
  - Removes filler words (um, uh, you know, like...)
  - Fixes sentence structure and punctuation
  - Adapts tone to context (formal for email, casual for chat)
  - Handles mixed Chinese/English naturally

Supports:
  - Ollama (local, privacy-preserving) — default
  - OpenAI
  - Anthropic
  - None (passthrough — just return raw transcript)
"""

from __future__ import annotations

from core.context import AppContext

SYSTEM_PROMPT = """You are a voice dictation assistant. Your job is to clean up raw speech transcription.

Rules:
- Remove filler words: um, uh, you know, like, actually, basically, sort of, kind of
- Fix run-on sentences, add punctuation
- Fix repeated words or false starts (e.g. "I I want to" → "I want to")
- Preserve the speaker's intended meaning exactly — do NOT paraphrase or summarize
- Preserve mixed language (Chinese/English code-switching) as spoken
- Adapt formality based on context:
  - Email/document: formal, complete sentences
  - Chat (Slack/Discord/Messages/Line): casual, natural
  - Terminal/code editor: keep technical terms exact, minimal cleanup
  - Default: neutral, clean prose
- Output ONLY the cleaned text. No explanations, no quotes, no markdown unless the context calls for it."""


def _build_user_prompt(raw_text: str, ctx: AppContext) -> str:
    context_fragment = ctx.to_prompt_fragment()
    parts = []
    if context_fragment:
        parts.append(f"Context:\n{context_fragment}")
    parts.append(f"Raw transcript:\n{raw_text}")
    return "\n\n".join(parts)


class PassthroughCleanup:
    """No-op — returns transcript as-is."""
    def clean(self, text: str, ctx: AppContext) -> str:
        return text.strip()


class OllamaCleanup:
    """Local LLM via Ollama. Keeps everything on-device."""

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434",
                 system_prompt: str = SYSTEM_PROMPT):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.system_prompt = system_prompt

    def clean(self, text: str, ctx: AppContext) -> str:
        import httpx

        with httpx.Client(timeout=30) as client:
            response = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": _build_user_prompt(text, ctx)},
                    ],
                },
            )
            response.raise_for_status()
        return response.json()["message"]["content"].strip()


class OpenAICleanup:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 system_prompt: str = SYSTEM_PROMPT):
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt

    def clean(self, text: str, ctx: AppContext) -> str:
        import httpx

        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": _build_user_prompt(text, ctx)},
                    ],
                },
            )
            response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()


class AnthropicCleanup:
    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001",
                 system_prompt: str = SYSTEM_PROMPT):
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt

    def clean(self, text: str, ctx: AppContext) -> str:
        import httpx

        with httpx.Client(timeout=30) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1024,
                    "system": self.system_prompt,
                    "messages": [
                        {"role": "user", "content": _build_user_prompt(text, ctx)},
                    ],
                },
            )
            response.raise_for_status()
        return response.json()["content"][0]["text"].strip()


def create_cleanup(config: dict):
    """
    config examples:
      {"backend": "none"}
      {"backend": "ollama", "model": "llama3.2"}
      {"backend": "openai", "api_key": "...", "model": "gpt-4o-mini"}
      {"backend": "anthropic", "api_key": "...", "model": "claude-haiku-4-5-20251001"}
    """
    backend = config.get("backend", "none")
    system_prompt = config.get("system_prompt") or SYSTEM_PROMPT

    if backend == "none":
        return PassthroughCleanup()
    elif backend == "ollama":
        return OllamaCleanup(
            model=config.get("model", "llama3.2"),
            base_url=config.get("base_url", "http://localhost:11434"),
            system_prompt=system_prompt,
        )
    elif backend == "openai":
        return OpenAICleanup(
            api_key=config["api_key"],
            model=config.get("model", "gpt-4o-mini"),
            system_prompt=system_prompt,
        )
    elif backend == "anthropic":
        return AnthropicCleanup(
            api_key=config["api_key"],
            model=config.get("model", "claude-haiku-4-5-20251001"),
            system_prompt=system_prompt,
        )
    else:
        raise ValueError(f"Unknown cleanup backend: {backend}")
