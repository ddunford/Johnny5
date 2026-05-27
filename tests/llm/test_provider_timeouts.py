"""OllamaProvider per-model timeout selection (deterministic — no network, no DB).

The reasoning model (qwen3.5) is a *thinking* model used only for heavy/OFFLINE
roles (deliberation fallback + the sleep roles' qwen fallback), where a ~150s
generation — its reasoning preamble + the structured JSON — is fine. The per-tick
fast model (gemma4) must keep a tight timeout so a hung local call fails over fast
and doesn't stall the heartbeat. This pins that split so a future refactor can't
silently collapse the two onto one timeout — which is exactly what made the qwen
self-model fallback time out before the per-model timeout landed (the ``@live`` leg
proves it end-to-end; this guards the selection logic cheaply).
"""

from __future__ import annotations

import httpx

from brain.llm.providers.ollama import OllamaProvider
from foundation.config import Settings


def _settings() -> Settings:
    return Settings(
        local_fast_model="gemma4:e4b",
        local_reasoning_model="qwen3.5-9b-128k:latest",
        local_llm_timeout=120.0,
        local_reasoning_timeout=240.0,
    )


async def test_reasoning_model_gets_the_wide_offline_timeout() -> None:
    provider = OllamaProvider(_settings())
    try:
        assert provider._request_timeout("qwen3.5-9b-128k:latest") == 240.0
    finally:
        await provider.aclose()


async def test_fast_model_uses_the_tight_client_default() -> None:
    provider = OllamaProvider(_settings())
    try:
        # The fast model defers to the client default (built from local_llm_timeout)...
        assert provider._request_timeout("gemma4:e4b") is httpx.USE_CLIENT_DEFAULT
        # ...and that client default is the tight per-tick budget, not the wide one.
        assert provider._client.timeout.read == 120.0
    finally:
        await provider.aclose()
