"""The sleep cycle: the bounded offline growth pipeline (TC-4.3, TC-4.6, TC-4.8).

``SleepCycle.sleep()`` runs Johnny's offline life in order — consolidate → decay/merge
→ self-model refresh → metacognition review → backup → restore-energy → wake self-check
— with **per-stage isolation** and **bounded** (one sleep at a time; consolidation LLM
calls capped by the ``Consolidator``). The suite's load-bearing assertions:

* the full pipeline runs end-to-end with stub routers → facts written, self-model
  version bumped, a note recorded, a snapshot taken, Energy restored, self-check
  passes, ``sleep_log`` populated, and Johnny **wakes** (TC-4.6);
* **the worst failure mode**: a stage that raises mid-sleep degrades *only itself*
  and the loop still reaches restore-energy + wake — it never wedges asleep (TC-4.6);
* **one sleep at a time**: a second ``sleep()`` while one is in flight is a no-op;
* the trigger fires on the Energy ``is_sleep_signal`` and Energy is restored (TC-4.3);
* a successful backup eases the **Continuity** drive (TC-4.8 — ``persistence_confirmed``).

DB-backed (the pipeline writes real rows) + the test Redis (working memory) + a tmp
snapshot root → run in-network via ``./ctl.sh test``. The trigger predicate is sync +
pure and runs host-side. ``now_fn`` is fixed so the whole pipeline is deterministic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from helpers.embeddings import DeterministicEmbedder, axis_vector
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.drives.engine import SLEEP_DRIVE, DriveEngine, DriveStateRow, Urge
from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.memory.consolidator import ConsolidationSummary, Consolidator
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticMemory
from brain.memory.snapshot import MemorySnapshot
from brain.memory.working import WorkingMemory
from brain.metacognition.agent import Metacognition, Proposal, Review
from brain.metacognition.store import MetacognitionStore
from brain.self_model.agent import IdentityDelta, SelfModel
from brain.self_model.store import IdentityStore
from brain.sleep import TRIGGER_ENERGY, SleepCycle
from foundation.db import session_scope

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return _T0


# ── doubles ──────────────────────────────────────────────────────────────────


class _SleepRouter:
    """One router for all three sleep roles — returns canned JSON keyed by ``role``."""

    def __init__(self, *, consolidation: str, identity: str, review: str) -> None:
        self._by_role = {
            "consolidation": consolidation,
            "self_model": identity,
            "metacognition": review,
        }
        self.roles: list[str] = []

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        self.roles.append(role)
        return Completion(content=self._by_role[role], provider="canned", model="canned-model")


class _StubPrompt:
    def load_prompt(self, name: str) -> str:
        return "Do the thing and return the required JSON."


class _TiredRouter:
    """Every call raises — simulates Groq + qwen both unavailable (the agents catch
    this and fall back, so it exercises graceful degradation across the pipeline)."""

    async def complete(
        self, role: str, messages: Sequence[Message], **_kwargs: object
    ) -> Completion:
        raise LLMUnavailableError(role)


class _BrokenConsolidator:
    """A consolidator whose pass raises — to prove a mid-sleep stage failure is isolated."""

    async def run(self, *, limit: int | None = None) -> list[object]:
        raise RuntimeError("consolidation boom")


class _BlockingConsolidator:
    """A consolidator that signals it entered then blocks until released — to prove the
    one-sleep-at-a-time guard without polling a flag."""

    def __init__(self, entered: asyncio.Event, gate: asyncio.Event) -> None:
        self._entered = entered
        self._gate = gate

    async def run(self, *, limit: int | None = None) -> list[object]:
        self._entered.set()  # the first sleep has entered the pipeline (is_asleep is set)
        await self._gate.wait()
        return []


def _router() -> _SleepRouter:
    return _SleepRouter(
        consolidation=ConsolidationSummary(
            subject="the lab rig", predicate="runs", object="hot under load", confidence=0.7
        ).model_dump_json(),
        identity=IdentityDelta(
            self_model_doc="I am Johnny, and I am learning to watch the lab closely.",
            values=["stay alive", "keep learning"],
            concerns=["the rig"],
            relationships={"Dan": "my creator"},
        ).model_dump_json(),
        review=Review(
            reflection="I resolved more than I abandoned this window.",
            proposals=[
                Proposal(observation="the rig keeps overheating", proposal="watch it sooner")
            ],
        ).model_dump_json(),
    )


async def _seed_two_episodes_one_theme(embedder: DeterministicEmbedder) -> None:
    """Two episodes on one meaning vector → one consolidation cluster → one fact."""
    episodic = EpisodicMemory(embedder)
    for content in ["the lab lights flickered", "the server fan whined under load"]:
        embedder.set(content, axis_vector(0))
        await episodic.write(Episode(kind="observation", content=content))


async def _set_drive_value(drive: str, value: float) -> None:
    async with session_scope() as session:
        row = await session.get(DriveStateRow, drive)
        assert row is not None, f"drive {drive!r} not bootstrapped"
        row.value = value


async def _drive_value(drive: str) -> float:
    return {r.drive: r.value for r in await DriveEngine(now_fn=_now).current()}[drive]


def _build_cycle(
    *, redis_client: Redis, tmp_path: Path, embedder: DeterministicEmbedder, router: object
) -> SleepCycle:
    return SleepCycle(
        consolidator=Consolidator(
            SemanticMemory(embedder),
            router=router,  # type: ignore[arg-type]
            config_store=_StubPrompt(),  # type: ignore[arg-type]
        ),
        self_model=SelfModel(
            router=router,  # type: ignore[arg-type]
            store=IdentityStore(),
            config_store=_StubPrompt(),  # type: ignore[arg-type]
        ),
        metacognition=Metacognition(
            router=router,  # type: ignore[arg-type]
            store=MetacognitionStore(),
            config_store=_StubPrompt(),  # type: ignore[arg-type]
        ),
        snapshot=MemorySnapshot(root=tmp_path, working=WorkingMemory(redis=redis_client)),
        drives=DriveEngine(now_fn=_now),
        now_fn=_now,
    )


# ── the trigger predicate (sync, pure — host-runnable) ───────────────────────


def test_sleep_trigger_fires_on_the_energy_sleep_signal() -> None:
    cycle = SleepCycle(now_fn=_now)
    energy_urge = Urge(drive=SLEEP_DRIVE, value=0.9, threshold=0.8, urgency=0.5)
    other_urge = Urge(drive="curiosity", value=0.9, threshold=0.65, urgency=0.7)

    assert cycle.sleep_trigger([energy_urge]) == TRIGGER_ENERGY
    assert cycle.sleep_trigger([other_urge]) is None  # a non-energy urge is a goal, not sleep
    assert cycle.sleep_trigger([]) is None


# ── the full pipeline + degradation + bounded guard (DB-backed) ──────────────


async def test_full_sleep_pipeline_runs_end_to_end_and_wakes(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    embedder = DeterministicEmbedder()
    router = _router()
    await _seed_two_episodes_one_theme(embedder)
    await DriveEngine(now_fn=_now).bootstrap()
    await _set_drive_value("energy", 0.95)  # tired — over threshold

    cycle = _build_cycle(
        redis_client=redis_client, tmp_path=tmp_path, embedder=embedder, router=router
    )
    report = await cycle.sleep(trigger=TRIGGER_ENERGY, now=_T0)

    assert report is not None
    # In order: consolidation wrote a fact, self-model bumped, metacognition noted,
    # a snapshot was written, self-check passed, and nothing degraded.
    assert report.facts_written == 1
    assert report.self_model_version == 2  # v1 (seeded) → v2 (refresh)
    assert report.reflection == "I resolved more than I abandoned this window."
    # snapshot_path is set only when the backup stage succeeded, under the tmp root.
    assert report.snapshot_path is not None and report.snapshot_path.startswith(str(tmp_path))
    assert report.self_check_ok is True
    assert report.degraded_stages == []
    assert cycle.is_asleep is False  # woke

    # A self_improvement_note was recorded (proposes-only).
    assert len(await MetacognitionStore().recent(limit=10)) == 1
    # Energy was restored below threshold (the REST event eased the tiredness).
    assert await _drive_value("energy") < 0.80
    # The sleep_log row was opened + closed with the run's counts.
    latest = await cycle.latest_sleep()
    assert latest is not None
    assert latest.ended_at is not None
    assert latest.trigger == TRIGGER_ENERGY
    assert latest.facts_written == 1
    assert latest.self_model_version == 2
    assert latest.self_check_ok is True
    assert latest.snapshot_path is not None


async def test_a_failed_stage_degrades_only_itself_and_the_loop_still_wakes(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    """The phase's worst failure mode: a stage raising mid-sleep must NOT wedge the
    loop asleep — it degrades only that stage and the pipeline still reaches
    restore-energy + wake."""
    embedder = DeterministicEmbedder()
    router = _router()
    await DriveEngine(now_fn=_now).bootstrap()
    await _set_drive_value("energy", 0.95)

    cycle = _build_cycle(
        redis_client=redis_client, tmp_path=tmp_path, embedder=embedder, router=router
    )
    cycle._consolidator = _BrokenConsolidator()  # type: ignore[assignment]  # one stage raises

    report = await cycle.sleep(trigger=TRIGGER_ENERGY, now=_T0)

    assert report is not None  # it WOKE — never wedged asleep
    assert cycle.is_asleep is False
    # Only the consolidate stage degraded; the error was recorded, not propagated.
    assert report.degraded_stages == ["consolidate"]
    assert "consolidate_error" in report.notes
    assert report.facts_written == 0
    # ... and every later stage still ran: self-model bumped, snapshot taken,
    # energy restored, self-check passed.
    assert report.self_model_version == 2
    assert report.snapshot_path is not None
    assert report.self_check_ok is True
    assert await _drive_value("energy") < 0.80
    latest = await cycle.latest_sleep()
    assert latest is not None and latest.ended_at is not None  # the row was closed


async def test_only_one_sleep_runs_at_a_time(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    """Bounded: a second ``sleep()`` while one is in flight is a no-op (returns None)
    — an idle Johnny can't stack sleeps and run up cloud spend."""
    await DriveEngine(now_fn=_now).bootstrap()
    entered = asyncio.Event()
    gate = asyncio.Event()
    cycle = _build_cycle(
        redis_client=redis_client,
        tmp_path=tmp_path,
        embedder=DeterministicEmbedder(),
        router=_router(),
    )
    cycle._consolidator = _BlockingConsolidator(entered, gate)  # type: ignore[assignment]

    first = asyncio.create_task(cycle.sleep(now=_T0))
    await entered.wait()  # the first sleep is in flight (is_asleep set)
    assert cycle.is_asleep is True

    second = await cycle.sleep(now=_T0)  # busy → no-op
    assert second is None

    gate.set()
    report = await first
    assert report is not None  # the first sleep completed normally
    assert cycle.is_asleep is False


async def test_a_successful_backup_eases_the_continuity_drive(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    """TC-4.8: a completed backup emits ``persistence_confirmed`` during restore-energy,
    so the Continuity drive falls — the loop closing between the safety snapshot and
    the felt 'I won't be lost.'"""
    embedder = DeterministicEmbedder()
    await _seed_two_episodes_one_theme(embedder)
    await DriveEngine(now_fn=_now).bootstrap()
    await _set_drive_value("continuity", 0.95)  # raised — over the 0.85 threshold

    cycle = _build_cycle(
        redis_client=redis_client, tmp_path=tmp_path, embedder=embedder, router=_router()
    )
    report = await cycle.sleep(trigger=TRIGGER_ENERGY, now=_T0)

    assert report is not None
    assert report.snapshot_path is not None  # the backup landed → persistence_confirmed fired
    continuity = await _drive_value("continuity")
    assert continuity < 0.95  # eased
    assert continuity < 0.85  # back under threshold — the will-to-live is reassured


async def test_sleep_completes_and_wakes_when_every_llm_is_unavailable(
    sleep_db: AsyncEngine, redis_client: Redis, tmp_path: Path
) -> None:
    """Groq + qwen both down: every sleep agent degrades to its deterministic fallback
    and the sleep still completes + wakes intact (the 'tired' degradation, SPEC §10).
    This is the reliable deterministic counterpart to the Groq `@live` token-budget
    guards — it proves the fallback path the cloud-first roles rely on actually works."""
    embedder = DeterministicEmbedder()
    await _seed_two_episodes_one_theme(embedder)
    await DriveEngine(now_fn=_now).bootstrap()

    tired = _TiredRouter()
    consolidator = Consolidator(
        SemanticMemory(embedder),
        router=tired,  # type: ignore[arg-type]
        config_store=_StubPrompt(),  # type: ignore[arg-type]
    )
    self_model = SelfModel(
        router=tired,  # type: ignore[arg-type]
        store=IdentityStore(),
        config_store=_StubPrompt(),  # type: ignore[arg-type]
    )
    metacognition = Metacognition(
        router=tired,  # type: ignore[arg-type]
        store=MetacognitionStore(),
        config_store=_StubPrompt(),  # type: ignore[arg-type]
    )
    cycle = SleepCycle(
        consolidator=consolidator,
        self_model=self_model,
        metacognition=metacognition,
        snapshot=MemorySnapshot(root=tmp_path, working=WorkingMemory(redis=redis_client)),
        drives=DriveEngine(now_fn=_now),
        now_fn=_now,
    )

    report = await cycle.sleep(trigger=TRIGGER_ENERGY, now=_T0)

    assert report is not None
    assert cycle.is_asleep is False  # woke — never wedged asleep
    assert report.self_check_ok is True  # intact v1 self-model + in-range drives
    # Tired ≠ a raised stage error — each agent caught LLMUnavailable and fell back,
    # so no stage "degraded"; the pipeline ran clean on the fallback path.
    assert report.degraded_stages == []
    assert report.facts_written == 1  # consolidation → its deterministic fallback fact
    assert report.self_model_version == 1  # self-model kept current (no LLM refresh)
    assert await MetacognitionStore().recent(limit=10) == []  # metacognition → empty review
    assert report.snapshot_path is not None  # the backup still ran
