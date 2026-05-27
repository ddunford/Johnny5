"""The LLM call log — one row per inference call, with cost.

Every call routed through `brain/llm/` is recorded here (FC-4): role, provider,
model, token counts, latency, status, and dollar cost. This table is the single
source of truth the budget governor reads to enforce the daily ceiling and trip
"tired" degradation (the Core reads it without importing this module, by
aggregating the table directly, so the Core stays decoupled from the Mind).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from foundation.db import Base

# The physical table name the Core's governor aggregates against. Keep in sync
# with that read path if it ever changes.
LLM_CALL_LOG_TABLE = "llm_call_log"


class LlmCallLog(Base):
    __tablename__ = LLM_CALL_LOG_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0"))

    # The budget governor sums cost over a time window; the provider breakdown
    # supports per-provider spend views and circuit diagnostics.
    __table_args__ = (
        Index("ix_llm_call_log_ts", "ts"),
        Index("ix_llm_call_log_provider_ts", "provider", "ts"),
    )
