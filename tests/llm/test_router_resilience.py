"""Router resilience: circuit breaker, "tired" failover, and recovery.

These are deterministic unit tests — no network. Providers are in-memory fakes
whose health we flip at will, and time is driven by the FrozenClock injected as
the router/breaker ``time_fn``. That lets us assert the open → half-open → closed
transitions and the 60s cooldown exactly, with zero ``sleep``.

Covers TC-0.4:
  * a failing primary fails over to the local provider, no exception surfaces;
  * the primary's circuit opens after N consecutive failures and is then skipped
    (the failing provider isn't even called);
  * after the cooldown the circuit half-opens and either recovers (on success)
    or re-opens (on a fresh failure);
  * breakers are per-provider (the healthy local one keeps serving);
  * when every provider in the chain is down, the router raises
    ``LLMUnavailableError`` rather than hanging or leaking a transport error.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from helpers.clock import FrozenClock
from pydantic import BaseModel

from brain.llm.base import (
    Completion,
    LLMProvider,
    LLMUnavailableError,
    Message,
    ProviderError,
)
from brain.llm.call_logger import CallLogger, CallLogRecord
from brain.llm.circuit_breaker import CircuitState
from brain.llm.router import LLMRouter
from brain.llm.routing import ModelStep, RoutingConfig

pytestmark = pytest.mark.resilience

ROLE = "deliberation"
GROQ = "groq"
OLLAMA = "ollama"
GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "gemma4:e4b"
THRESHOLD = 3
COOLDOWN = 60.0


class FakeProvider:
    """An in-memory LLMProvider whose success/failure is toggleable."""

    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self._fail = fail
        self.calls = 0

    def set_fail(self, fail: bool) -> None:
        self._fail = fail

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
        self.calls += 1
        if self._fail:
            raise ProviderError(f"{self.name} is down", provider=self.name)
        return Completion(content="ok", provider=self.name, model=model, completion_tokens=1)

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


class MemoryCallLogger:
    """Captures call records in memory so tests can assert FC-4 logging."""

    def __init__(self) -> None:
        self.records: list[CallLogRecord] = []

    async def record(self, entry: CallLogRecord) -> None:
        self.records.append(entry)


class ScriptedProvider:
    """An LLMProvider that returns a fixed script of completions/exceptions.

    Each ``complete`` call pops the next scripted item; an Exception item is
    raised, a Completion item is returned. Used to drive the retry-with-feedback
    path (invalid → valid JSON on the same provider) deterministically.
    """

    def __init__(self, name: str, script: list[Completion | Exception]) -> None:
        self.name = name
        self._script = list(script)
        self.calls = 0

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
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


class _Answer(BaseModel):
    """Minimal schema used to exercise the router's JSON validation + retry."""

    answer: str


def _completion(provider: str, content: str) -> Completion:
    return Completion(content=content, provider=provider, model="m", completion_tokens=1)


def _build_router(
    groq: LLMProvider,
    ollama: LLMProvider,
    clock: FrozenClock,
    logger: CallLogger,
    *,
    schema_retries: int = 0,
) -> LLMRouter:
    routing = RoutingConfig(
        roles={
            ROLE: [
                ModelStep(provider=GROQ, model=GROQ_MODEL),
                ModelStep(provider=OLLAMA, model=OLLAMA_MODEL),
            ]
        }
    )
    providers: dict[str, LLMProvider] = {GROQ: groq, OLLAMA: ollama}
    return LLMRouter(
        providers=providers,
        routing=routing,
        call_logger=logger,
        failure_threshold=THRESHOLD,
        reset_timeout=COOLDOWN,
        schema_retries=schema_retries,
        time_fn=clock,
    )


async def _call(router: LLMRouter) -> Completion:
    return await router.complete(ROLE, [Message(role="user", content="ping")])


# --------------------------------------------------------------------------- #
# Failover ("tired" degradation)                                              #
# --------------------------------------------------------------------------- #


async def test_failover_to_local_when_primary_fails() -> None:
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, FrozenClock(), MemoryCallLogger())

    completion = await _call(router)

    assert completion.provider == OLLAMA  # served by the local model, transparently
    assert groq.calls == 1 and ollama.calls == 1


async def test_no_exception_bubbles_while_local_is_healthy() -> None:
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, FrozenClock(), MemoryCallLogger())

    # Many calls across the open transition — the caller never sees an error.
    for _ in range(THRESHOLD + 5):
        completion = await _call(router)
        assert completion.provider == OLLAMA


async def test_all_providers_down_raises_llm_unavailable() -> None:
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=True)
    router = _build_router(groq, ollama, FrozenClock(), MemoryCallLogger())

    with pytest.raises(LLMUnavailableError) as exc_info:
        await _call(router)
    assert exc_info.value.role == ROLE


# --------------------------------------------------------------------------- #
# Circuit opening + skip                                                      #
# --------------------------------------------------------------------------- #


async def test_circuit_opens_after_threshold_consecutive_failures() -> None:
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, FrozenClock(), MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)

    assert router.circuit_states()[GROQ] is CircuitState.OPEN
    assert groq.calls == THRESHOLD
    # The healthy local breaker stays closed — breakers are per-provider.
    assert router.circuit_states()[OLLAMA] is CircuitState.CLOSED


async def test_open_circuit_is_skipped_without_calling_provider() -> None:
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, FrozenClock(), MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)
    assert groq.calls == THRESHOLD  # opened now

    # Further calls must NOT touch the open provider — straight to local.
    for _ in range(3):
        completion = await _call(router)
        assert completion.provider == OLLAMA
    assert groq.calls == THRESHOLD  # unchanged: open circuit skipped


# --------------------------------------------------------------------------- #
# Cooldown → half-open → recover / re-open (frozen clock)                     #
# --------------------------------------------------------------------------- #


async def test_circuit_stays_open_until_cooldown_elapses() -> None:
    clock = FrozenClock()
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, clock, MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)
    assert router.circuit_states()[GROQ] is CircuitState.OPEN

    clock.advance(COOLDOWN - 0.1)  # just shy of the cooldown
    assert router.circuit_states()[GROQ] is CircuitState.OPEN
    await _call(router)
    assert groq.calls == THRESHOLD  # still skipped


async def test_circuit_half_opens_after_cooldown_and_retries_primary() -> None:
    clock = FrozenClock()
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, clock, MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)
    assert router.circuit_states()[GROQ] is CircuitState.OPEN

    clock.advance(COOLDOWN)  # cooldown reached → half-open
    assert router.circuit_states()[GROQ] is CircuitState.HALF_OPEN

    # The half-open trial actually calls the primary again.
    await _call(router)
    assert groq.calls == THRESHOLD + 1


async def test_recovers_to_primary_when_it_comes_back() -> None:
    clock = FrozenClock()
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, clock, MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)
    clock.advance(COOLDOWN)
    groq.set_fail(False)  # primary recovers

    completion = await _call(router)

    assert completion.provider == GROQ  # back on the primary
    assert router.circuit_states()[GROQ] is CircuitState.CLOSED
    # And it stays on the primary for subsequent calls (no needless failover).
    assert (await _call(router)).provider == GROQ


async def test_half_open_failure_reopens_circuit() -> None:
    clock = FrozenClock()
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, clock, MemoryCallLogger())

    for _ in range(THRESHOLD):
        await _call(router)
    clock.advance(COOLDOWN)
    assert router.circuit_states()[GROQ] is CircuitState.HALF_OPEN

    # Still failing → the half-open trial re-opens the circuit.
    completion = await _call(router)
    assert completion.provider == OLLAMA  # failover still works
    assert router.circuit_states()[GROQ] is CircuitState.OPEN

    # Re-opened cooldown is honoured from the new opened-at.
    clock.advance(COOLDOWN - 0.1)
    assert router.circuit_states()[GROQ] is CircuitState.OPEN
    clock.advance(0.1)
    assert router.circuit_states()[GROQ] is CircuitState.HALF_OPEN


# --------------------------------------------------------------------------- #
# Call logging (FC-4)                                                         #
# --------------------------------------------------------------------------- #


async def test_each_attempt_is_logged_including_failover() -> None:
    logger = MemoryCallLogger()
    groq = FakeProvider(GROQ, fail=True)
    ollama = FakeProvider(OLLAMA, fail=False)
    router = _build_router(groq, ollama, FrozenClock(), logger)

    await _call(router)

    # One failed primary attempt + one successful local attempt, both logged.
    by_provider = {(r.provider, r.status) for r in logger.records}
    assert (GROQ, "error") in by_provider
    assert (OLLAMA, "ok") in by_provider
    assert all(r.role == ROLE for r in logger.records)


# --------------------------------------------------------------------------- #
# Retry-with-feedback + schema failover                                       #
# --------------------------------------------------------------------------- #


async def test_schema_failure_retries_same_provider_with_feedback() -> None:
    """Invalid JSON then valid JSON → the router re-prompts the SAME provider
    once and returns the valid completion (no failover)."""
    logger = MemoryCallLogger()
    groq = ScriptedProvider(
        GROQ,
        [_completion(GROQ, "this is not json"), _completion(GROQ, '{"answer": "ok"}')],
    )
    ollama = ScriptedProvider(OLLAMA, [_completion(OLLAMA, '{"answer": "local"}')])
    router = _build_router(groq, ollama, FrozenClock(), logger, schema_retries=1)

    completion = await router.complete(ROLE, [Message(role="user", content="ping")], schema=_Answer)

    assert completion.provider == GROQ
    assert completion.content == '{"answer": "ok"}'
    assert groq.calls == 2  # retried the same provider once
    assert ollama.calls == 0  # no failover needed
    # Logged the bad attempt then the good one, both on groq.
    assert [(r.provider, r.status) for r in logger.records] == [
        (GROQ, "schema_error"),
        (GROQ, "ok"),
    ]


async def test_schema_failure_fails_over_without_tripping_breaker() -> None:
    """A provider that keeps producing invalid JSON exhausts its retries and the
    router fails over — but the breaker stays CLOSED, because the transport was
    healthy (bad output is not a provider outage)."""
    logger = MemoryCallLogger()
    groq = ScriptedProvider(
        GROQ,
        [_completion(GROQ, "nope one"), _completion(GROQ, "nope two")],
    )
    ollama = ScriptedProvider(OLLAMA, [_completion(OLLAMA, '{"answer": "local"}')])
    router = _build_router(groq, ollama, FrozenClock(), logger, schema_retries=1)

    completion = await router.complete(ROLE, [Message(role="user", content="ping")], schema=_Answer)

    assert completion.provider == OLLAMA  # failed over to local
    assert groq.calls == 2  # both schema attempts used
    # Crucially: a schema failure must NOT trip the circuit.
    assert router.circuit_states()[GROQ] is CircuitState.CLOSED
    assert router.circuit_states()[OLLAMA] is CircuitState.CLOSED
    statuses = [(r.provider, r.status) for r in logger.records]
    assert statuses == [(GROQ, "schema_error"), (GROQ, "schema_error"), (OLLAMA, "ok")]
