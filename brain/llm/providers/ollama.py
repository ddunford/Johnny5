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

from typing import Any

import httpx

from brain.llm.providers.openai_compatible import OpenAICompatibleProvider
from foundation.config import Settings

OLLAMA_PROVIDER_NAME = "ollama"


class OllamaProvider(OpenAICompatibleProvider):
    """Local provider with a per-model timeout: tight for the fast model, wide for qwen.

    The client default (``local_llm_timeout``) keeps the per-tick fast model (gemma4)
    on a snappy failover budget; the reasoning model (qwen3.5) — a thinking model used
    only for heavy/offline roles — gets the wider ``local_reasoning_timeout`` so its
    ~150s reasoning-preamble generation completes instead of timing out. Without the
    split, a single global timeout forces a bad trade: either qwen times out (offline
    growth collapses on the fallback path) or the hot path waits too long on a hang.
    """

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            name=OLLAMA_PROVIDER_NAME,
            base_url=settings.local_llm_base_url.rstrip("/") + "/v1",
            api_key=None,
            timeout=settings.local_llm_timeout,
        )
        self._reasoning_model = settings.local_reasoning_model
        self._reasoning_timeout = settings.local_reasoning_timeout

    def _request_timeout(self, model: str) -> Any:
        """Widen the timeout for the (offline) reasoning model; fast model keeps the default."""
        if model == self._reasoning_model:
            return self._reasoning_timeout
        return httpx.USE_CLIENT_DEFAULT
