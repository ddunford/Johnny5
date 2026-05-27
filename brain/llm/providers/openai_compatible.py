"""Shared OpenAI-compatible chat-completions transport.

Both Groq and Ollama speak the OpenAI ``/chat/completions`` shape, so the HTTP
call and response parsing live here once. The parser also handles thinking
models that return their reasoning in a separate ``message.reasoning`` channel
(verified for ``qwen3.5-9b-128k`` on inference.lan).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from brain.llm.base import Completion, Message, ProviderError, ProviderTimeoutError


def parse_chat_completion(
    data: dict[str, Any], *, provider: str, requested_model: str
) -> Completion:
    """Project an OpenAI-shaped chat-completion envelope into a ``Completion``.

    Pure (no I/O) so contract tests can feed captured fixtures straight through
    it. Handles the thinking-model case: ``content`` is the answer; a separate
    ``message.reasoning`` channel is kept distinct, and only used as the answer
    if ``content`` came back blank (defensive guard).
    """
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"{provider} returned no choices", provider=provider)

    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    # Different servers name the thinking channel differently.
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    effective_content = content if content.strip() else (reasoning or "")

    usage = data.get("usage") or {}
    return Completion(
        content=effective_content,
        reasoning=reasoning,
        provider=provider,
        model=data.get("model") or requested_model,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        finish_reason=choice.get("finish_reason"),
        raw=data,
    )


class OpenAICompatibleProvider:
    """A provider that talks the OpenAI chat-completions wire format.

    ``base_url`` must point at the OpenAI-compatible root (the segment before
    ``chat/completions``). A trailing slash is enforced so httpx joins the path
    without dropping the prefix.
    """

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.name = name
        normalised = base_url.rstrip("/") + "/"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=normalised, headers=headers, timeout=timeout)

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        if stop:
            payload["stop"] = stop

        try:
            resp = await self._client.post(
                "chat/completions", json=payload, timeout=self._request_timeout(model)
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"{self.name} timed out calling chat/completions", provider=self.name
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name} transport error: {exc}", provider=self.name) from exc

        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} returned HTTP {resp.status_code}: {resp.text[:300]}",
                provider=self.name,
                status_code=resp.status_code,
            )

        return self._parse(resp.json(), requested_model=model)

    def _request_timeout(self, model: str) -> Any:
        """Per-request timeout for ``model``; base uses the client default.

        Hook for providers that serve models with very different latency profiles
        (e.g. Ollama's fast gemma4 vs the slow qwen thinking model) — they override
        this to widen the timeout for the heavy model without slowing the others.
        """
        return httpx.USE_CLIENT_DEFAULT

    def _parse(self, data: dict[str, Any], *, requested_model: str) -> Completion:
        """Project a response envelope into a ``Completion`` (delegates to the
        pure ``parse_chat_completion``; kept as an instance method so it carries
        the provider name and is the single parse path for prod + contract tests)."""
        return parse_chat_completion(data, provider=self.name, requested_model=requested_model)

    async def aclose(self) -> None:
        await self._client.aclose()
