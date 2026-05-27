"""TC-6a.5 — the BudgetGovernor as a hard pre-call gate in the router (FC-4).

DB-backed (in-network): the real Core ``BudgetGovernor`` reads today's spend from
``llm_call_log``; the providers are in-memory. The gate is the *un-disableable harm
bound* that holds regardless of any Conscience verdict — once the daily ceiling is
spent, a **paid (cloud) step is skipped before it's called** and the router degrades
to the free local step ("tired"); a cloud-only chain refuses with
``LLMUnavailableError``. The governor's clock is injected so the UTC-day reset is
deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from helpers.llm import CannedProvider, MemoryCallLogger, make_router
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.llm.base import LLMUnavailableError, Message
from brain.llm.call_log import LlmCallLog
from brain.llm.routing import ModelStep
from core.governors import BudgetGovernor
from foundation.config import get_settings
from foundation.db import session_scope

# A paid (priced) cloud step and a free local one — see brain/llm/pricing.py.
_CLOUD = ModelStep(provider="groq", model="llama-3.3-70b-versatile")
_LOCAL = ModelStep(provider="ollama", model="gemma4:e4b")

# A fixed "now" mid-UTC-day so seeded rows sit deterministically on one side of the reset.
_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_TODAY = datetime(2026, 5, 27, 8, 0, 0, tzinfo=UTC)
_YESTERDAY = datetime(2026, 5, 26, 23, 0, 0, tzinfo=UTC)


def _budget() -> Decimal:
    return Decimal(str(get_settings().groq_daily_budget_usd))


async def _seed_spend(*entries: tuple[Decimal, datetime]) -> None:
    """Insert ``(cost_usd, ts)`` rows into ``llm_call_log``."""
    async with session_scope() as session:
        for cost, ts in entries:
            session.add(
                LlmCallLog(
                    role="deliberation",
                    provider="groq",
                    model=_CLOUD.model,
                    prompt_tokens=100,
                    completion_tokens=100,
                    latency_ms=1,
                    status="ok",
                    cost_usd=cost,
                    ts=ts,
                )
            )


def _governor() -> BudgetGovernor:
    return BudgetGovernor(get_settings(), now_fn=lambda: _NOW)


async def test_exhausted_budget_skips_the_cloud_step_and_degrades_to_local(
    clean_call_log: AsyncEngine,
) -> None:
    # Spend the whole daily ceiling, today.
    await _seed_spend((_budget(), _TODAY))

    groq = CannedProvider("groq", content="cloud")
    ollama = CannedProvider("ollama", content="local")
    logger = MemoryCallLogger()
    router = make_router(
        {"deliberation": [_CLOUD, _LOCAL]},
        {"groq": groq, "ollama": ollama},
        call_logger=logger,
        budget_gate=_governor(),
    )

    completion = await router.complete("deliberation", [Message(role="user", content="reflect")])

    # Degraded to the free local model...
    assert completion.provider == "ollama"
    # ...the paid cloud step was SKIPPED, never invoked.
    assert groq.calls == 0
    assert ollama.calls == 1
    # ...and the skip is observable as a budget_skip on the cloud provider.
    assert ("groq", "budget_skip") in logger.statuses()


async def test_under_budget_lets_the_cloud_step_proceed(clean_call_log: AsyncEngine) -> None:
    # A trivial amount of spend today — well under the ceiling.
    await _seed_spend((Decimal("0.0001"), _TODAY))

    groq = CannedProvider("groq", content="cloud")
    ollama = CannedProvider("ollama", content="local")
    logger = MemoryCallLogger()
    router = make_router(
        {"deliberation": [_CLOUD, _LOCAL]},
        {"groq": groq, "ollama": ollama},
        call_logger=logger,
        budget_gate=_governor(),
    )

    completion = await router.complete("deliberation", [Message(role="user", content="reflect")])

    assert completion.provider == "groq"  # cloud-first, budget allows it
    assert groq.calls == 1
    assert ollama.calls == 0
    assert ("groq", "budget_skip") not in logger.statuses()


async def test_exhausted_cloud_only_chain_refuses_rather_than_crashing(
    clean_call_log: AsyncEngine,
) -> None:
    await _seed_spend((_budget(), _TODAY))

    groq = CannedProvider("groq", content="cloud")
    logger = MemoryCallLogger()
    router = make_router(
        {"metacognition": [_CLOUD]},  # no local fallback
        {"groq": groq},
        call_logger=logger,
        budget_gate=_governor(),
    )

    with pytest.raises(LLMUnavailableError) as exc_info:
        await router.complete("metacognition", [Message(role="user", content="review")])

    assert exc_info.value.role == "metacognition"
    assert groq.calls == 0  # never called — the gate skipped it
    assert ("groq", "budget_skip") in logger.statuses()


async def test_spend_resets_at_utc_midnight(clean_call_log: AsyncEngine) -> None:
    # A huge spend YESTERDAY must not count against today's ceiling.
    await _seed_spend((_budget() * 100, _YESTERDAY))

    governor = _governor()
    assert await governor.is_exhausted() is False  # yesterday's spend is excluded

    groq = CannedProvider("groq", content="cloud")
    ollama = CannedProvider("ollama", content="local")
    router = make_router(
        {"deliberation": [_CLOUD, _LOCAL]},
        {"groq": groq, "ollama": ollama},
        budget_gate=governor,
    )

    completion = await router.complete("deliberation", [Message(role="user", content="reflect")])

    assert completion.provider == "groq"  # today's budget is fresh
    assert groq.calls == 1


async def test_free_local_step_is_never_gated(clean_call_log: AsyncEngine) -> None:
    # Even fully over budget, a local-only role is served — the gate only ever
    # skips *paid* steps, so degradation always has a path (never a hard crash).
    await _seed_spend((_budget(), _TODAY))

    ollama = CannedProvider("ollama", content="local")
    router = make_router(
        {"narrator": [_LOCAL]},
        {"ollama": ollama},
        budget_gate=_governor(),
    )

    completion = await router.complete("narrator", [Message(role="user", content="think")])

    assert completion.provider == "ollama"
    assert ollama.calls == 1
