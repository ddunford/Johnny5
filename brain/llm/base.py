"""Shared contract for the LLM layer: messages, completions, the provider
protocol, and the error hierarchy the router's circuit breaker keys off.

These types are the seam between the router and concrete providers. A model's
output-shape change can only ripple through here, which is why every adapter
that parses a response has a contract test against a captured envelope.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """A chat message. ``content`` is a string for text, or the OpenAI
    multimodal parts list (used by the vision client for image inputs)."""

    role: Role
    content: str | list[dict[str, Any]]


class Completion(BaseModel):
    """A normalised completion, provider-agnostic.

    ``content`` is the text the caller should use. ``reasoning`` carries a
    thinking model's chain-of-thought when the provider exposes it separately
    (e.g. ``qwen3.5-9b-128k`` via Ollama) — kept distinct so cognition uses the
    answer, not the scratch-work, while observability can still see both.
    """

    content: str
    reasoning: str | None = None
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)


class LLMError(Exception):
    """Base for all LLM-layer errors."""


class ProviderError(LLMError):
    """A provider call failed (HTTP error, bad payload, empty response)."""

    def __init__(self, message: str, *, provider: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ProviderTimeoutError(ProviderError):
    """A provider call exceeded its timeout."""


class SchemaValidationError(LLMError):
    """A model response could not be validated against the requested schema.

    Carries the offending ``content`` so the router can quote it back to the
    model on a retry-with-feedback.
    """

    def __init__(self, message: str, *, content: str = "") -> None:
        super().__init__(message)
        self.content = content


class LLMUnavailableError(LLMError):
    """Every provider in a role's chain failed or was circuit-open.

    This is the "fully tired" terminal state — the caller (the cognitive cycle)
    degrades gracefully rather than crashing.
    """

    def __init__(self, role: str, cause: Exception | None = None) -> None:
        super().__init__(f"no provider available for role {role!r}: {cause}")
        self.role = role
        self.cause = cause


@runtime_checkable
class LLMProvider(Protocol):
    """The contract every provider adapter implements.

    Adapters are dumb transports: they translate ``Message``/params to the
    provider's wire format, parse the response into a ``Completion``, and raise
    ``ProviderError`` on failure. Routing, circuit breaking, retry, cost logging,
    and fallback all live in the router — never in an adapter.
    """

    name: str

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        stop: list[str] | None = None,
    ) -> Completion: ...

    async def aclose(self) -> None: ...
