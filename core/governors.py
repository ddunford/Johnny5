"""Resource governors — the Core's spend reader.

The budget governor reads the day's LLM spend from ``llm_call_log`` so the Core
can later enforce the daily ceiling and trigger "tired" degradation. Enforcement
(blocking calls, forcing local-only) lands in Phase 6; this phase ships the
read-only accounting.

The Core stays decoupled from the Mind (FC-1): it aggregates the call-log table
by name rather than importing ``brain``'s ORM model. The table name below must
track the one the LLM layer writes to.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text

from foundation.config import Settings
from foundation.db import session_scope

# Must match brain.llm.call_log.LLM_CALL_LOG_TABLE. Referenced by name so the
# Core never imports the Mind.
LLM_CALL_LOG_TABLE = "llm_call_log"


@dataclass(frozen=True)
class BudgetStatus:
    spent_usd: Decimal
    budget_usd: Decimal
    remaining_usd: Decimal
    exhausted: bool


class BudgetGovernor:
    """Reads daily LLM spend against the configured ceiling.

    Day boundaries are UTC midnight. The clock is injectable so tests can place
    rows on either side of the reset deterministically.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._budget = Decimal(str(settings.groq_daily_budget_usd))
        self._now_fn = now_fn

    @property
    def daily_budget_usd(self) -> Decimal:
        return self._budget

    def _day_start(self) -> datetime:
        now = self._now_fn()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def spent_since(self, since: datetime) -> Decimal:
        """Total USD spend recorded at or after ``since``."""
        async with session_scope() as session:
            result = await session.execute(
                text(
                    f"SELECT COALESCE(SUM(cost_usd), 0) FROM {LLM_CALL_LOG_TABLE} "  # noqa: S608 - constant table name
                    "WHERE ts >= :since"
                ),
                {"since": since},
            )
            return Decimal(str(result.scalar_one()))

    async def spent_today(self) -> Decimal:
        """Total USD spend since the start of the current UTC day."""
        return await self.spent_since(self._day_start())

    async def status(self) -> BudgetStatus:
        """A snapshot of today's spend versus the ceiling."""
        spent = await self.spent_today()
        remaining = self._budget - spent
        return BudgetStatus(
            spent_usd=spent,
            budget_usd=self._budget,
            remaining_usd=remaining if remaining > 0 else Decimal("0"),
            exhausted=spent >= self._budget,
        )

    async def is_exhausted(self) -> bool:
        """Whether today's spend has reached the daily ceiling."""
        return (await self.spent_today()) >= self._budget
