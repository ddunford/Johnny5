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


def _build_router(
    groq: FakeProvider,
    ollama: FakeProvider,
    clock: FrozenClock,
    logger: CallLogger,
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
        schema_retries=0,
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
