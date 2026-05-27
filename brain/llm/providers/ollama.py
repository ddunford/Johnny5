"""Ollama provider — local inference on inference.lan (both RTX 3060s).

Serves the local workhorse ``gemma4:e4b`` (clean ``content``, multimodal) and the
heavier on-demand ``qwen3.5-9b-128k``. Ollama's OpenAI-compatible API lives under
``/v1``, so that segment is appended to the configured base URL.

The qwen tag is a *thinking* model: it returns its chain-of-thought in
``message.reasoning`` and may leave ``content`` empty. The shared transport's
parser handles both — surfacing reasoning when content is blank — so callers get
a usable ``Completion`` either way. No auth: local network only.

**``/no_think`` for structured output (SPEC §10).** On a *schema* (json_object)
role the reasoning model's chain-of-thought is pure waste — it burns the token
budget before any JSON (``finish=length``, empty content; the Phase-4 trap). The
fix verified on this box (2026-05-27): the OpenAI-compat ``/v1`` endpoint **ignores**
a ``think`` field, but Ollama's **native** ``/api/chat`` honours ``think: false``,
and with ``format: "json"`` qwen emits clean JSON directly (no preamble). So for the
reasoning model on a schema role we route to the native endpoint with thinking off;
every other call (the fast model, free-text reasoning) keeps the ``/v1`` path.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from brain.llm.base import Completion, Message, ProviderError, ProviderTimeoutError
from brain.llm.providers.openai_compatible import OpenAICompatibleProvider
from foundation.config import Settings

OLLAMA_PROVIDER_NAME = "ollama"

# Ollama's native chat endpoint (NOT the /v1 OpenAI-compat shim) — the only one
# that honours ``think`` on this deployment.
_NATIVE_CHAT_PATH = "/api/chat"
# Native structured-output format: ask for plain JSON (the router then validates it
# against the role's Pydantic schema, with retry-with-feedback on a mismatch).
_NATIVE_JSON_FORMAT = "json"


def parse_native_chat(data: dict[str, Any], *, requested_model: str) -> Completion:
    """Project an Ollama **native** ``/api/chat`` envelope into a ``Completion``.

    Pure (no I/O) so a contract test can feed a captured native envelope straight
    through it. The native shape differs from OpenAI's: the thinking channel is
    ``message.thinking`` (empty when ``think:false``), token counts are
    ``prompt_eval_count`` / ``eval_count``, and the stop reason is ``done_reason``.
    ``content``-first, falling back to ``thinking`` only if content is blank.
    """
    message = data.get("message") or {}
    content = message.get("content") or ""
    reasoning = message.get("thinking") or None
    effective_content = content if content.strip() else (reasoning or "")
    return Completion(
        content=effective_content,
        reasoning=reasoning,
        provider=OLLAMA_PROVIDER_NAME,
        model=data.get("model") or requested_model,
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        completion_tokens=int(data.get("eval_count") or 0),
        finish_reason=data.get("done_reason"),
        raw=data,
    )


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
        # Native endpoint (absolute URL) for the /no_think path — sits beside, not
        # under, the /v1 shim the base client targets.
        self._native_chat_url = settings.local_llm_base_url.rstrip("/") + _NATIVE_CHAT_PATH

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        stop: list[str] | None = None,
    ) -> Completion:
        """Route the reasoning model on a schema role to the native ``/no_think`` path.

        A *schema* call (``response_format`` set) on the reasoning model goes to the
        native endpoint with ``think:false`` + ``format:json`` so qwen emits clean
        JSON with no chain-of-thought preamble. Everything else (the fast model, and
        free-text reasoning where the preamble is harmless) keeps the ``/v1`` path.
        """
        if model == self._reasoning_model and response_format is not None:
            return await self._complete_no_think(
                messages, model=model, temperature=temperature, max_tokens=max_tokens
            )
        return await super().complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            stop=stop,
        )

    async def _complete_no_think(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """Call the native ``/api/chat`` with thinking off and JSON format."""
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
            "think": False,
            "format": _NATIVE_JSON_FORMAT,
            "options": options,
        }

        try:
            resp = await self._client.post(
                self._native_chat_url, json=payload, timeout=self._request_timeout(model)
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{self.name} timed out calling {_NATIVE_CHAT_PATH}", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport error: {exc}", provider=self.name) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} returned HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        return parse_native_chat(resp.json(), requested_model=model)

    def _request_timeout(self, model: str) -> Any:
        """Widen the timeout for the (offline) reasoning model; fast model keeps the default."""
        if model == self._reasoning_model:
            return self._reasoning_timeout
        return httpx.USE_CLIENT_DEFAULT
