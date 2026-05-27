"""Seeding helpers + shared constants for the Phase-5a web-API suite.

These build the **known rows** the read endpoints project, using the production
store write APIs (never raw SQL) so a seed is exactly what the loop would have
persisted. They are deliberately store-level (not endpoint-level): the same seeds
back both the behavioural shape tests (``tests/api/test_web_api_*.py``) and the
captured wire fixtures (``tests/api/test_wire_fixtures.py``), so the populated
fixture is the real projection of a real row.

Cross-loop discipline (the Phase-0/1 gotcha, carried): every store persists
through ``foundation.db.session_scope()`` (the process-global engine). So a seed
**must run inside the same event loop that owns the installed loop-local engine**
— i.e. inside the TestClient app's lifespan (the portal loop), exactly as the WS
suite preseeds thoughts inside ``_build_app``'s lifespan. Calling these from a
plain ``@pytest_asyncio.fixture`` on a *different* loop would hit the "attached to
a different loop" error. The app-builder calls them; tests don't call them direct.

Embeddings are injected (``DeterministicEmbedder``) so seeding never depends on the
live BGE-M3 server — episodic/semantic writes embed through the stub, keeping the
captured fixtures byte-stable across runs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from helpers.embeddings import DeterministicEmbedder

from brain.goals.store import Goal, GoalStore
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticFact, SemanticMemory
from brain.metacognition.store import MetacognitionStore, SelfImprovementNote
from brain.self_model.store import IdentityDoc, IdentityStore
from brain.sleep import SleepLogStore, SleepReport
from brain.workspace import Workspace, WorkspaceEvent

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
    "self_improvement_note",
    "identity",
    "sleep_log",
    "goal",
    "mood",
    "drive_state",
)


# ── bus log: thoughts + audit (incl. the FC-5 action.dispatched dispatch point) ──


async def seed_thought(workspace: Workspace, text: str) -> WorkspaceEvent:
    """Broadcast (persist) one ``thought`` event — the /thoughts + /ws backfill row."""
    return await workspace.broadcast(
        WorkspaceEvent(module="narrator", type="thought", payload={"text": text})
    )


async def seed_event(
    workspace: Workspace, *, module: str, type: str, payload: Mapping[str, Any] | None = None
) -> WorkspaceEvent:
    """Broadcast (persist) an arbitrary bus event — used to plant the ``action.dispatched``
    audit row (FC-5) and ``state``/``drive.*``/``mood`` rows the /audit filter slices on."""
    return await workspace.broadcast(
        WorkspaceEvent(module=module, type=type, payload=dict(payload or {}))
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


# ── goals (an active incumbent + a resolved one for the recent list) ─────────────


async def seed_active_goal(
    *, source: str, description: str, priority: float = 0.7, plan: Mapping[str, Any] | None = None
) -> Goal:
    """Promote one active goal — the /goals ``active`` list."""
    return await GoalStore().promote(
        Goal(source=source, description=description, priority=priority, plan=dict(plan or {}))
    )


async def seed_resolved_goal(
    *, source: str, description: str, outcome: Mapping[str, Any] | None = None
) -> Goal:
    """Promote then resolve a goal — populates the /goals ``recent`` (closed) list."""
    store = GoalStore()
    goal = await store.promote(Goal(source=source, description=description))
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


async def seed_note(*, observation: str, proposal: str) -> SelfImprovementNote:
    """Append one self-improvement note (status=open) — a /self ``notes`` row."""
    return await MetacognitionStore().add_note(
        SelfImprovementNote(observation=observation, proposal=proposal)
    )
