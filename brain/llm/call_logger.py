"""Call-log sink for the router.

Every inference attempt (success, error, timeout, schema failure) is recorded to
``llm_call_log`` (FC-4). The sink is an injectable protocol so the router can be
exercised without a database in resilience tests, while production writes real
rows the budget governor reads.

Writing the log must never break cognition: a logging failure is swallowed and
reported, never raised into the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from brain.llm.call_log import LlmCallLog
from foundation.db import session_scope
from foundation.observability import get_logger

_log = get_logger("brain.llm.call_logger")


@dataclass(frozen=True)
class CallLogRecord:
    role: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    cost_usd: Decimal


class CallLogger(Protocol):
    async def record(self, entry: CallLogRecord) -> None: ...


class DatabaseCallLogger:
    """Persists call records to ``llm_call_log``; swallows write failures."""

    async def record(self, entry: CallLogRecord) -> None:
        try:
            async with session_scope() as session:
                session.add(
                    LlmCallLog(
                        role=entry.role,
                        provider=entry.provider,
                        model=entry.model,
                        prompt_tokens=entry.prompt_tokens,
                        completion_tokens=entry.completion_tokens,
                        latency_ms=entry.latency_ms,
                        status=entry.status,
                        cost_usd=entry.cost_usd,
                    )
                )
        except Exception:  # never let logging break the cycle
            _log.exception(
                "call_log.write_failed",
                role=entry.role,
                provider=entry.provider,
                model=entry.model,
                status=entry.status,
            )
