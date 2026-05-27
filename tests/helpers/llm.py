"""In-memory LLM doubles for deterministic router/agent tests (no network).

The router-resilience suite grew its own ``FakeProvider``/``MemoryCallLogger``
locally; the 6a substrate tests (Conscience vetting, the budget gate) need the
same shapes, so they live here once rather than being copy-pasted. A provider
returns a fixed completion (or raises) and records the messages it was handed —
enough to drive the Conscience's verdict deterministically *and* assert the
git-backed prompt (FC-3) actually reached the model.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from brain.llm.base import Completion, LLMProvider, Message, ProviderError
from brain.llm.call_logger import CallLogger, CallLogRecord
from brain.llm.router import BudgetGate, LLMRouter
from brain.llm.routing import ModelStep, RoutingConfig


class MemoryCallLogger:
    """Captures call records in memory so a test can assert FC-4 logging / budget skips."""

    def __init__(self) -> None:
        self.records: list[CallLogRecord] = []

    async def record(self, entry: CallLogRecord) -> None:
        self.records.append(entry)

    def statuses(self) -> list[tuple[str, str]]:
        """``(provider, status)`` for every logged attempt, in order."""
        return [(r.provider, r.status) for r in self.records]


class CannedProvider:
    """An ``LLMProvider`` that returns one fixed completion (or raises) per call.

    Records ``calls`` and the ``last_messages`` it saw, so a test can assert the
    Conscience sent its loaded prompt as the system message (the FC-3 wiring) and
    that the verdict is whatever the model said (no code-side floor).
    """

    def __init__(self, name: str, *, content: str = "ok", fail: bool = False) -> None:
        self.name = name
        self._content = content
        self._fail = fail
        self.calls = 0
        self.last_messages: list[Message] = []

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
        self.last_messages = list(messages)
        if self._fail:
            raise ProviderError(f"{self.name} is down", provider=self.name)
        return Completion(
            content=self._content, provider=self.name, model=model, completion_tokens=1
        )

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        return None


def make_router(
    roles: dict[str, list[ModelStep]],
    providers: dict[str, LLMProvider],
    *,
    call_logger: CallLogger | None = None,
    budget_gate: BudgetGate | None = None,
    schema_retries: int = 0,
) -> LLMRouter:
    """Build a real :class:`LLMRouter` over in-memory providers (no network)."""
    return LLMRouter(
        providers=providers,
        routing=RoutingConfig(roles=roles),
        call_logger=call_logger or MemoryCallLogger(),
        budget_gate=budget_gate,
        schema_retries=schema_retries,
    )
