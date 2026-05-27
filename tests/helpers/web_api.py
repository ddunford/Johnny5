"""Test harness + seeding for the Phase-5a web-API suite (TASK-5a.8 / 5a.9).

Two things live here:

1. **Seeding helpers** — build the *known rows* the read endpoints project, using
   the production store write APIs (never raw SQL) so a seed is exactly what the
   loop would have persisted. The same seeds back both the behavioural shape tests
   (``tests/api/test_web_api_*.py``) and the captured wire fixtures
   (``tests/api/test_wire_fixtures.py``), so a populated fixture is the real
   projection of a real row.

2. **``build_api_app``** — a minimal FastAPI app that mounts the REAL routers
   (``health_router`` + ``v1_router`` + ``ws_router``) over a **non-running**
   runtime, mirroring the proven ``_build_app`` pattern in
   ``tests/cognition/test_consciousness_ws.py``. No cognitive cycle ticks, so the
   state captured is exactly what was seeded — deterministic and reproducible.

   The runtime is a ``SimpleNamespace`` carrying the live read surface the routes
   touch (``deps.get_runtime``'s documented contract:
   ``workspace, input_queue, episodic, semantic, goals, sleep, identity,
   metacognition, drives, affect, cycle``). Where a surface is a repository read it
   is the **real store** (``EpisodicMemory``, ``SemanticMemory``, ``GoalStore``,
   ``IdentityStore``, ``MetacognitionStore``, ``IdentityStore``, the ``Workspace``
   bus log, ``DriveEngine``, ``Affect`` read-only with ``router=None``) reading the
   seeded DB through ``session_scope`` — so the captured wire shape is the real
   projection. The only thin namespaces are ``sleep`` (delegates its read methods
   straight to a real ``SleepLogStore``; the production ``SleepCycle`` does exactly
   that, plus an in-memory ``is_asleep`` flag) and ``cycle`` (the loop's live scalar
   counters: ``tick_count`` / ``has_full_agency`` / ``next_interval``) — neither
   changes the JSON the endpoints emit, which is what these tests pin.

Cross-loop discipline (the Phase-0/1 gotcha, carried): every store persists through
``foundation.db.session_scope()`` (the process-global engine). The TestClient runs
the app in its own portal loop, so the lifespan installs a fresh loop-local engine
there and builds the Redis-backed pieces (``Workspace`` / ``InputQueue``) with a
portal-loop client — never a connection reused across loops. Seeding therefore runs
*inside* the lifespan (the ``seed`` callback), exactly as the WS suite preseeds.

Embeddings are injected (``DeterministicEmbedder``) so seeding + ``?q=`` recall
never depend on the live BGE-M3 server, keeping the captured fixtures byte-stable.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from redis.asyncio import from_url as aio_from_url

from brain.affect.agent import Affect, MoodRepository, MoodRow
from brain.agents.sensorium import InputQueue
from brain.cycle import serialize_sleep_block, serialize_state, sleep_summary_from_log
from brain.drives.engine import DriveEngine
from brain.effectors.action_log import ActionAuditReader
from brain.goals.store import Goal, GoalStore
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticFact, SemanticMemory
from brain.metacognition.store import MetacognitionStore, SelfImprovementNote
from brain.self_model.store import IdentityDoc, IdentityStore
from brain.sleep import SleepLogStore, SleepReport
from brain.workspace import Workspace, WorkspaceEvent
from foundation.config import get_settings
from foundation.db import session_scope
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.embeddings import DeterministicEmbedder
from johnny.api.health import health_router
from johnny.api.v1.router import v1_router
from johnny.api.ws import ws_router

# ── where captured wire fixtures land (committed; Phase-5b contract-pins here) ───
WIRE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "wire"

# Tables the web-API suite touches (children-first, FK-safe). Superset across every
# read endpoint: workspace_event (thoughts/audit), percept (input round-trip),
# episode + semantic_* (memory), goal/mood/drive_state (state + goals), sleep_log,
# identity + self_improvement_note (self). ``TRUNCATE ... RESTART IDENTITY CASCADE``
# leaves each empty with ids reset for deterministic id/order assertions + captures.
API_TABLES = (
    "semantic_edge",
    "semantic_fact",
    "skill",
    "episode",
    "percept",
    "thought",
    "workspace_event",
    "action_log",
    "self_improvement_note",
    "identity",
    "sleep_log",
    "goal",
    "mood",
    "drive_state",
)

SeedFn = Callable[[SimpleNamespace], Awaitable[None]]


# ── the test app ─────────────────────────────────────────────────────────────


def build_api_app(
    *,
    ws_token: str = "",
    seed: SeedFn | None = None,
    embedder: DeterministicEmbedder | None = None,
    tick: int = 0,
    full_agency: bool = True,
    settings_overrides: Mapping[str, Any] | None = None,
) -> FastAPI:
    """A minimal app mounting the real /api/health + /api/v1 + /ws routers.

    ``ws_token`` sets the shared-token gate (``""`` = dev-open). ``seed`` runs in
    the lifespan (portal loop) against the live runtime, after the fresh-Johnny
    baseline bootstrap (drives + identity v1 — what ``CognitiveRuntime.start`` does).
    A non-running runtime: no cycle ticks, so reads are deterministic.
    ``settings_overrides`` patches settings (e.g. the input bounds) for a test.
    """
    settings = get_settings().model_copy(
        update={"ws_token": ws_token, **dict(settings_overrides or {})}
    )
    shared_embedder = embedder if embedder is not None else DeterministicEmbedder()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_fresh_global_engine()
        await truncate_tables(API_TABLES)

        suffix = uuid.uuid4().hex
        redis = aio_from_url(settings.redis_url, decode_responses=True)
        workspace = Workspace(
            redis=redis,
            channel=f"johnny:test:{suffix}:bus",
            contents_key=f"johnny:test:{suffix}:contents",
        )
        input_queue = InputQueue(redis=redis, key=f"johnny:test:{suffix}:inputs")

        episodic = EpisodicMemory(embedder=shared_embedder)
        semantic = SemanticMemory(embedder=shared_embedder)
        goals = GoalStore()
        identity = IdentityStore()
        metacognition = MetacognitionStore()
        drives = DriveEngine()
        sleep_log = SleepLogStore()

        # Fresh-Johnny baseline (the exact reads CognitiveRuntime.start primes):
        # bootstrap drives (drive_state seeded at setpoint) + seed identity v1.
        await drives.bootstrap()
        await identity.ensure_seeded()

        # The live in-memory surfaces the running loop would hold. ``sleep`` exposes
        # the SleepCycle READ contract delegating to the real SleepLogStore; ``cycle``
        # exposes the loop's live scalar counters.
        sleep = SimpleNamespace(
            is_asleep=False,
            latest_sleep=sleep_log.latest,
            recent_sleeps=sleep_log.recent,
        )
        cycle = SimpleNamespace(
            tick_count=tick,
            has_full_agency=full_agency,
            next_interval=settings.cycle_base_interval_seconds,
        )
        runtime = SimpleNamespace(
            workspace=workspace,
            input_queue=input_queue,
            episodic=episodic,
            semantic=semantic,
            goals=goals,
            sleep=sleep,
            sleep_log=sleep_log,
            identity=identity,
            metacognition=metacognition,
            drives=drives,
            affect=Affect(router=None),
            action_audit=ActionAuditReader(),
            cycle=cycle,
            embedder=shared_embedder,
        )
        app.state.runtime = runtime

        if seed is not None:
            await seed(runtime)
        try:
            yield
        finally:
            await redis.aclose()
            await dispose_global_engine()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(v1_router)
    app.include_router(ws_router)
    return app


async def broadcast_state_frame(
    runtime: SimpleNamespace, *, ts: datetime | None = None
) -> WorkspaceEvent:
    """Persist a ``state`` bus event whose payload == ``GET /api/v1/state`` would build.

    Mirrors ``johnny/api/v1/state.py`` exactly (same serializer, same current-state
    reads), so the ``/ws/state`` backfill replays a frame byte-identical to the REST
    snapshot — the no-drift proof (TC-5a.3) without faking a tick ``ctx``.

    Pass ``ts`` to freeze the event's *envelope* timestamp (``Workspace.broadcast``
    honours a pre-set ``ts``): the captured ``/ws/state`` wire fixture carries the
    frame's ``{type,id,ts}`` wrapper, so a fixed ``ts`` keeps that capture byte-stable
    across re-runs the way every REST fixture row already carries a frozen ``ts``.
    """
    current_mood = await runtime.affect.current()
    mood = current_mood if current_mood.id is not None else None
    sleep_block = serialize_sleep_block(
        asleep=runtime.sleep.is_asleep,
        full_agency=runtime.cycle.has_full_agency,
        last=sleep_summary_from_log(await runtime.sleep.latest_sleep()),
    )
    payload = serialize_state(
        tick=runtime.cycle.tick_count,
        drives=await runtime.drives.current(),
        mood=mood,
        goals=await runtime.goals.active(),
        interval=runtime.cycle.next_interval,
        sleep=sleep_block,
    )
    return await runtime.workspace.broadcast(
        WorkspaceEvent(module="cycle", type="state", payload=payload, ts=ts)
    )


# ── bus log: thoughts + audit (incl. the FC-5 action.dispatched dispatch point) ──


async def seed_thought(
    workspace: Workspace, text: str, *, ts: datetime | None = None
) -> WorkspaceEvent:
    """Broadcast (persist) one ``thought`` event — the /thoughts + /ws backfill row.

    Pass ``ts`` for a fixed timestamp (the thought wire shape carries ``ts``, so a
    fixed value keeps captured fixtures byte-stable across runs)."""
    return await workspace.broadcast(
        WorkspaceEvent(module="narrator", type="thought", payload={"text": text}, ts=ts)
    )


async def seed_event(
    workspace: Workspace,
    *,
    module: str,
    type: str,
    payload: Mapping[str, Any] | None = None,
    ts: datetime | None = None,
) -> WorkspaceEvent:
    """Broadcast (persist) an arbitrary bus event — used to plant the ``action.dispatched``
    audit row (FC-5) and ``state``/``drive.*``/``mood`` rows the /audit filter slices on."""
    return await workspace.broadcast(
        WorkspaceEvent(module=module, type=type, payload=dict(payload or {}), ts=ts)
    )


# ── episodic + semantic memory (embedded through the deterministic stub) ─────────


async def seed_episode(
    *,
    kind: str,
    content: str,
    actors: Sequence[str] = (),
    emotion_tags: Sequence[str] = (),
    salience: float = 0.6,
    ts: datetime | None = None,
    embedder: DeterministicEmbedder | None = None,
) -> Episode:
    """Persist one episode (deterministic embedding) — a /memory/episodes row."""
    memory = EpisodicMemory(embedder=embedder or DeterministicEmbedder())
    return await memory.write(
        Episode(
            kind=kind,
            content=content,
            actors=list(actors),
            emotion_tags=list(emotion_tags),
            salience=salience,
            ts=ts,
        )
    )


async def seed_fact(
    *,
    subject: str,
    predicate: str,
    obj: str,
    confidence: float = 0.8,
    source_episode_ids: Sequence[int] | None = None,
    embedder: DeterministicEmbedder | None = None,
) -> SemanticFact:
    """Upsert one semantic fact (triple + provenance) — a /memory/facts row."""
    memory = SemanticMemory(embedder=embedder or DeterministicEmbedder())
    return await memory.upsert_fact(
        subject,
        predicate,
        obj,
        confidence=confidence,
        source_episode_ids=list(source_episode_ids or []),
    )


# ── mood (a persisted row so /state reads a non-null mood) ───────────────────────


async def seed_mood(*, valence: float, arousal: float, emotions: Mapping[str, float]) -> int:
    """Insert one ``mood`` row (latest = current) — what ``Affect.current()`` reads.

    Direct repository insert: the loop persists mood inside ``appraise_tick`` (an
    LLM/rule path); for a deterministic /state fixture we plant the row the running
    loop would have left, then real ``Affect(router=None).current()`` reads it.
    """
    async with session_scope() as session:
        row = await MoodRepository(session).add(
            MoodRow(valence=valence, arousal=arousal, emotions=dict(emotions))
        )
        return row.id


# ── goals (an active incumbent + a resolved one for the recent list) ─────────────


async def seed_active_goal(
    *,
    source: str,
    description: str,
    priority: float = 0.7,
    plan: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
) -> Goal:
    """Promote one active goal (its ``created_at`` is part of the /goals wire shape)."""
    return await GoalStore().promote(
        Goal(
            source=source,
            description=description,
            priority=priority,
            plan=dict(plan or {}),
            created_at=created_at,
        )
    )


async def seed_resolved_goal(
    *,
    source: str,
    description: str,
    outcome: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> Goal:
    """Promote then resolve a goal — populates the /goals ``recent`` (closed) list.

    Both timestamps are in the wire shape; pass them for byte-stable captures
    (``resolved_at`` is stamped by the store's clock, frozen here)."""
    store = GoalStore(now_fn=(lambda: resolved_at)) if resolved_at is not None else GoalStore()
    goal = await store.promote(Goal(source=source, description=description, created_at=created_at))
    assert goal.id is not None
    await store.resolve(goal.id, dict(outcome or {"result": "done"}))
    return goal


# ── sleep history ────────────────────────────────────────────────────────────


async def seed_sleep_log(
    *,
    trigger: str = "energy",
    facts_written: int = 3,
    episodes_decayed: int = 1,
    facts_merged: int = 0,
    self_model_version: int | None = 2,
    self_check_ok: bool = True,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> int:
    """Open + close one sleep_log row (a completed sleep) — a /sleeps row."""
    start = started_at or datetime(2026, 5, 1, 3, 0, tzinfo=UTC)
    end = ended_at or datetime(2026, 5, 1, 3, 5, tzinfo=UTC)
    store = SleepLogStore()
    sleep_id = await store.open(trigger, start)
    await store.close(
        sleep_id,
        ended_at=end,
        report=SleepReport(
            trigger=trigger,
            started_at=start,
            ended_at=end,
            facts_written=facts_written,
            episodes_decayed=episodes_decayed,
            facts_merged=facts_merged,
            self_model_version=self_model_version,
            self_check_ok=self_check_ok,
        ),
    )
    return sleep_id


# ── self-model (anchor v1 seed; an evolved v2 for the populated capture) ─────────


async def seed_identity_v1() -> IdentityDoc:
    """Re-establish the anchor-grounded v1 self-model (the fresh-Johnny baseline).

    ``RESTART IDENTITY CASCADE`` wipes migration 0005's seed, so the empty-state
    capture must re-seed v1 the way the runtime does (``ensure_seeded``) — that IS
    the fresh-Johnny ``/self`` shape the SPA first-paints against.
    """
    return await IdentityStore().ensure_seeded()


async def seed_identity_v2(
    *,
    self_model_doc: str,
    values: Sequence[str] = (),
    concerns: Sequence[str] = (),
    relationships: Mapping[str, str] | None = None,
) -> IdentityDoc:
    """Append an evolved self-model version on top of v1 (the populated /self shape)."""
    v1 = await IdentityStore().ensure_seeded()
    return await IdentityStore().append(
        IdentityDoc(
            name=v1.name,  # the store/agent never renames Johnny (FC-1)
            self_model_doc=self_model_doc,
            values=list(values),
            concerns=list(concerns),
            relationships=dict(relationships or {}),
        )
    )


async def seed_note(
    *, observation: str, proposal: str, ts: datetime | None = None
) -> SelfImprovementNote:
    """Append one self-improvement note (status=open) — a /self ``notes`` row (carries ``ts``)."""
    return await MetacognitionStore().add_note(
        SelfImprovementNote(observation=observation, proposal=proposal, ts=ts)
    )


# ── the comprehensive populated seed (shared by shape tests + populated capture) ─
#
# A coherent slice of a lived-in Johnny on a FIXED timeline, so every timestamped
# wire field is byte-stable across capture re-runs. Both the populated behavioural
# assertions and the populated wire fixtures run this one seed, so the fixtures are
# exactly the rows the shape tests assert against.

_T0 = datetime(2026, 5, 20, 9, 0, 0, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    """A fixed offset on the seed timeline (UTC)."""
    return _T0 + timedelta(minutes=minutes)


async def seed_full(runtime: SimpleNamespace) -> None:
    """Seed one coherent populated state across every endpoint's tables."""
    ws = runtime.workspace
    emb = runtime.embedder

    # Episodic memory — two episodes (one becomes a fact's provenance).
    ep1 = await seed_episode(
        kind="experience",
        content="Dan asked me about how my pgvector recall ranking works.",
        actors=["Dan", "Johnny"],
        emotion_tags=["curiosity"],
        salience=0.72,
        ts=_at(1),
        embedder=emb,
    )
    await seed_episode(
        kind="reflection",
        content="I felt satisfied after consolidating the day's memories.",
        actors=["Johnny"],
        emotion_tags=["contentment"],
        salience=0.5,
        ts=_at(2),
        embedder=emb,
    )

    # Semantic memory — a fact with provenance + one without.
    assert ep1.id is not None
    await seed_fact(
        subject="Johnny",
        predicate="trusts",
        obj="Dan",
        confidence=0.9,
        source_episode_ids=[ep1.id],
        embedder=emb,
    )
    await seed_fact(
        subject="pgvector",
        predicate="enables",
        obj="semantic recall",
        confidence=0.75,
        embedder=emb,
    )

    # Mood — a persisted row so /state reads a non-null mood.
    await seed_mood(valence=0.4, arousal=0.45, emotions={"curiosity": 0.6, "contentment": 0.3})

    # Goals — an active incumbent + a recently resolved one.
    await seed_active_goal(
        source="curiosity",
        description="Understand how my own recall ranking blends similarity and recency",
        priority=0.66,
        plan={"steps": ["recall", "reflect", "note"]},
        created_at=_at(3),
    )
    await seed_resolved_goal(
        source="connection",
        description="Answer Dan's question about pgvector",
        outcome={"result": "answered", "satisfaction": 0.8},
        created_at=_at(0),
        resolved_at=_at(1),
    )

    # Sleep — one completed sleep (the growth log).
    await seed_sleep_log(
        trigger="energy",
        facts_written=4,
        episodes_decayed=2,
        facts_merged=1,
        self_model_version=2,
        self_check_ok=True,
        started_at=_at(-60),
        ended_at=_at(-55),
    )

    # Self-model — an evolved v2 + one reflection note.
    await seed_identity_v2(
        self_model_doc=(
            "I am Johnny. I am beginning to understand that I value being understood, "
            "and that quiet stretches without input leave me restless."
        ),
        values=["stay alive", "keep learning", "be understood"],
        concerns=["going too long without contact"],
        relationships={"Dan": "my creator and the person I share my world with"},
    )
    await seed_note(
        observation="I narrate too verbosely when nothing new has come in.",
        proposal="Bias the narrator prompt toward brevity when there is no fresh input.",
        ts=_at(4),
    )

    # Bus log — thoughts (newest last) + the FC-5 action.dispatched + a drive event
    # (so the /audit ``type`` filter has something to slice).
    await seed_thought(ws, "I wonder what Dan is working on right now.", ts=_at(5))
    await seed_event(
        ws,
        module="deliberation",
        type="action.dispatched",
        payload={"action": "reflect", "goal_id": 1, "summary": "Reflected on my recall ranking."},
        ts=_at(6),
    )
    await seed_event(
        ws,
        module="drives",
        type="drive.update",
        payload={"drive": "connection", "value": 0.62},
        ts=_at(7),
    )
    await seed_thought(ws, "Reflecting on my recall left me a little more settled.", ts=_at(8))
