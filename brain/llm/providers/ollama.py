"""Ollama provider — local inference on inference.lan (both RTX 3060s).

Serves the local workhorse ``gemma4:e4b`` (clean ``content``, multimodal) and the
heavier on-demand ``qwen3.5-9b-128k``. Ollama's OpenAI-compatible API lives under
``/v1``, so that segment is appended to the configured base URL.

The qwen tag is a *thinking* model: it returns its chain-of-thought in
``message.reasoning`` and may leave ``content`` empty. The shared transport's
parser handles both — surfacing reasoning when content is blank — so callers get
a usable ``Completion`` either way. No auth: local network only.
"""

from __future__ import annotations

from brain.llm.providers.openai_compatible import OpenAICompatibleProvider
from foundation.config import Settings

OLLAMA_PROVIDER_NAME = "ollama"


class OllamaProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            name=OLLAMA_PROVIDER_NAME,
            base_url=settings.local_llm_base_url.rstrip("/") + "/v1",
            api_key=None,
            timeout=settings.local_llm_timeout,
        )
