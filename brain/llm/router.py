"""The LLM router — the single doorway to inference (FC-4).

`LLMRouter.complete(role, messages, schema=...)` picks the role's provider chain,
enforces a per-provider circuit breaker, retries-with-feedback on schema
validation failure, falls back down the chain ("tired" degradation), and logs
every attempt to ``llm_call_log`` with cost. No inner agent ever calls a provider
directly.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from time import perf_counter

from pydantic import BaseModel, ValidationError

from brain.llm.base import (
    Completion,
    LLMProvider,
    LLMUnavailableError,
    Message,
    ProviderError,
    ProviderTimeoutError,
    SchemaValidationError,
)
from brain.llm.call_logger import CallLogger, CallLogRecord, DatabaseCallLogger
from brain.llm.circuit_breaker import CircuitBreaker, CircuitState
from brain.llm.pricing import estimate_cost
from brain.llm.providers.groq import GroqProvider
from brain.llm.providers.ollama import OllamaProvider
from brain.llm.routing import ModelStep, RoutingConfig, load_routing_config
from foundation.config import Settings
from foundation.observability import get_logger

_log = get_logger("brain.llm.router")

_JSON_RESPONSE_FORMAT = {"type": "json_object"}


class LLMRouter:
    def __init__(
        self,
        *,
        providers: dict[str, LLMProvider],
        routing: RoutingConfig,
        call_logger: CallLogger,
        failure_threshold: int = 4,
        reset_timeout: float = 60.0,
        schema_retries: int = 1,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers = providers
        self._routing = routing
        self._call_logger = call_logger
        self._schema_retries = schema_retries
        self._breakers = {
            name: CircuitBreaker(
                failure_threshold=failure_threshold,
                reset_timeout=reset_timeout,
                time_fn=time_fn,
            )
            for name in providers
        }
        self._validate_routing_providers()

    def _validate_routing_providers(self) -> None:
        for role, steps in self._routing.roles.items():
            for step in steps:
                if step.provider not in self._providers:
                    raise ValueError(f"role {role!r} routes to unknown provider {step.provider!r}")

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        """Complete a prompt for a cognitive role, falling back down the chain."""
        chain = self._routing.chain_for(role)
        last_error: Exception | None = None

        for step in chain:
            breaker = self._breakers[step.provider]
            if not breaker.allow():
                _log.debug("router.skip_open_circuit", role=role, provider=step.provider)
                continue
            try:
                completion = await self._invoke_with_feedback(
                    step, role, messages, schema, temperature, max_tokens
                )
            except ProviderError as exc:
                # Transport/HTTP failure — count it against the provider's health.
                breaker.record_failure()
                last_error = exc
                _log.warning(
                    "router.provider_failed",
                    role=role,
                    provider=step.provider,
                    model=step.model,
                    error=str(exc),
                )
                continue
            except SchemaValidationError as exc:
                # The provider is healthy; it just produced unusable output after
                # retries. Don't trip the breaker — fail over to the next step.
                breaker.record_success()
                last_error = exc
                _log.warning(
                    "router.schema_failover",
                    role=role,
                    provider=step.provider,
                    model=step.model,
                )
                continue
            else:
                breaker.record_success()
                return completion

        raise LLMUnavailableError(role, last_error)

    async def _invoke_with_feedback(
        self,
        step: ModelStep,
        role: str,
        messages: Sequence[Message],
        schema: type[BaseModel] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """Call one provider, retrying-with-feedback on schema validation failure."""
        provider = self._providers[step.provider]
        attempt_messages = list(messages)

        for attempt in range(self._schema_retries + 1):
            try:
                return await self._attempt(
                    provider, step.model, role, attempt_messages, schema, temperature, max_tokens
                )
            except SchemaValidationError as err:
                if attempt >= self._schema_retries:
                    raise
                # Quote the bad output back and ask for valid JSON, then retry.
                attempt_messages = [
                    *attempt_messages,
                    Message(role="assistant", content=err.content),
                    Message(
                        role="user",
                        content=(
                            f"Your previous response was invalid: {err}. "
                            "Respond with ONLY valid JSON matching the required schema."
                        ),
                    ),
                ]
        # Unreachable: the loop either returns or raises on the final attempt.
        raise AssertionError("retry loop exited without result")

    async def _attempt(
        self,
        provider: LLMProvider,
        model: str,
        role: str,
        messages: Sequence[Message],
        schema: type[BaseModel] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> Completion:
        """One provider call: time it, validate, and log exactly once."""
        response_format = _JSON_RESPONSE_FORMAT if schema is not None else None
        start = perf_counter()

        try:
            completion = await provider.complete(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except ProviderTimeoutError:
            await self._log(role, provider.name, model, None, "timeout", start)
            raise
        except ProviderError:
            await self._log(role, provider.name, model, None, "error", start)
            raise

        if schema is not None:
            try:
                schema.model_validate_json(completion.content)
            except ValidationError as exc:
                await self._log(role, provider.name, model, completion, "schema_error", start)
                raise SchemaValidationError(
                    f"response did not match {schema.__name__}: {exc}",
                    content=completion.content,
                ) from exc

        await self._log(role, provider.name, model, completion, "ok", start)
        return completion

    async def _log(
        self,
        role: str,
        provider: str,
        model: str,
        completion: Completion | None,
        status: str,
        start: float,
    ) -> None:
        latency_ms = int((perf_counter() - start) * 1000)
        prompt_tokens = completion.prompt_tokens if completion else 0
        completion_tokens = completion.completion_tokens if completion else 0
        await self._call_logger.record(
            CallLogRecord(
                role=role,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                status=status,
                cost_usd=estimate_cost(provider, model, prompt_tokens, completion_tokens),
            )
        )

    def circuit_states(self) -> dict[str, CircuitState]:
        """Current circuit state per provider (for the health endpoint)."""
        return {name: breaker.state for name, breaker in self._breakers.items()}

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()


def build_router(settings: Settings) -> LLMRouter:
    """Construct the production router from settings (Groq + Ollama + DB logging)."""
    providers: dict[str, LLMProvider] = {
        "groq": GroqProvider(settings),
        "ollama": OllamaProvider(settings),
    }
    routing = load_routing_config(settings)
    return LLMRouter(
        providers=providers,
        routing=routing,
        call_logger=DatabaseCallLogger(),
        failure_threshold=settings.circuit_failure_threshold,
        reset_timeout=settings.circuit_reset_seconds,
        schema_retries=settings.llm_schema_retries,
    )
