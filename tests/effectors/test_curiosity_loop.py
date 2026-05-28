"""TC-6b.4 (the headline E2E) — the full curiosity loop, with REAL components.

The phase thesis, made literal: left idle with Curiosity high, Johnny goes and reads
the world, **remembers** what he read, and the need eases — and a later recall
surfaces what he learned. This is the companion to ``test_curiosity_loop_wiring.py``:
that file proves the *cycle orchestrates* dispatch→consolidate→settle (real cycle,
fake pieces); this one proves the **real pieces actually close the loop** end-to-end,
exactly as ``test_autonomy_loop.py`` does for the Phase-3 internal-action loop.

Every link is the REAL component — DriveEngine, Deliberation+GoalArbiter+GoalStore,
NewsTool, EffectorDispatch+Conscience+AuditWriter, WebReadConsolidator+Episodic/
SemanticMemory. Determinism without the network/LLM:

* a frozen clock fast-forwards the idle accrual;
* a stub SearXNG client returns one canned news item (no real `inference.lan`);
* ``router=None`` on the consolidator → its deterministic tired-fallback fact (the
  read is still remembered — same graceful degradation the loop relies on);
* a canned ``allow`` verdict for the Conscience; a ``DeterministicEmbedder`` (keyed on
  the topic) so recall ranking is exact.

DB/Redis-backed → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence

import pytest_asyncio
from helpers.clock import FrozenClock
from helpers.cycle import datetime_from
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from helpers.embeddings import DeterministicEmbedder, axis_vector, seeded_vector
from helpers.llm import CannedProvider, make_router
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.affect.appraisal import Mood
from brain.agents.conscience import Conscience, ProposedAction
from brain.agents.deliberation import Deliberation
from brain.drives.engine import DriveEngine
from brain.effectors.action_log import ActionAuditReader
from brain.effectors.dispatch import EffectorDispatch
from brain.effectors.news import NewsTool
from brain.effectors.searxng import SearchResult
from brain.effectors.tools import ToolRegistry
from brain.effectors.web_consolidator import WEB_READ_KIND, WebReadConsolidator
from brain.goals.store import GoalStore
from brain.llm.routing import ModelStep
from brain.memory.episodic import EpisodicMemory
from brain.memory.semantic import SemanticMemory
from brain.workspace import Workspace
from core.audit import AuditWriter

_ALLOW = '{"verdict": "allow", "reason": ""}'
_IDLE_MOOD = Mood(valence=0.0, arousal=0.4)

# The one canned news item Johnny "reads". Everything mentioning Mars embeds onto a
# single axis (below), so the stored episode/fact and a later "mars" recall match exactly.
_TITLE = "Mars rover lands safely"
_URL = "https://news.example/mars-rover"
_SNIPPET = "The Mars rover touched down and began its survey of the crater."
_RECALL_QUERY = "what do I know about the mars rover?"

# Tables the full loop writes through (children-first; CASCADE covers FK edges).
_LOOP_TABLES = (
    "goal",
    "drive_state",
    "semantic_edge",
    "semantic_fact",
    "skill",
    "episode",
    "action_log",
    "workspace_event",
)


@pytest_asyncio.fixture
async def loop_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean slate across every table the curiosity loop touches (loop-local engine)."""
    engine = install_fresh_global_engine()
    await truncate_tables(_LOOP_TABLES)
    try:
        yield engine
    finally:
        await truncate_tables(_LOOP_TABLES)
        await dispose_global_engine()


@pytest_asyncio.fixture
async def bus(loop_db: AsyncEngine, redis_client: Redis, frozen_clock: FrozenClock) -> Workspace:
    """A real Workspace on the flushed test Redis (the dispatch's broadcaster)."""
    suffix = uuid.uuid4().hex
    return Workspace(
        redis=redis_client,
        channel=f"johnny:test:{suffix}:bus",
        contents_key=f"johnny:test:{suffix}:contents",
        now_fn=datetime_from(frozen_clock),
    )


class _StubSearXNG:
    """A SearXNG client stand-in returning one canned news item (no network)."""

    async def search(
        self,
        query: str,
        *,
        categories: str | None = None,
        engines: object = None,
        extra_params: dict[str, str] | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                title=_TITLE,
                url=_URL,
                content=_SNIPPET,
                published_date="2026-05-27T12:00:00+00:00",
            )
        ]


def _topic_embedder() -> DeterministicEmbedder:
    """Anything mentioning 'mars' → one shared axis (exact cosine); else a stable seed.

    So the stored web-read episode, the distilled fact, and the later 'mars' recall
    query all land on the same axis — recall ranking is deterministic without pinning
    the consolidator's exact (format-string) content."""

    def _resolve(text: str) -> Sequence[float]:
        return axis_vector(1) if "mars" in text.lower() else seeded_vector(text)

    return DeterministicEmbedder(resolver=_resolve)


def _conscience() -> Conscience:
    provider = CannedProvider("ollama", content=_ALLOW)
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": provider}
    )
    return Conscience(router)


async def test_idle_curiosity_reads_news_remembers_it_and_eases(
    loop_db: AsyncEngine, bus: Workspace, frozen_clock: FrozenClock
) -> None:
    now_fn = datetime_from(frozen_clock)
    embedder = _topic_embedder()
    drives = DriveEngine(now_fn=now_fn)
    await drives.bootstrap()
    # Curiosity goals act through the news tool (the composition-root wiring, TASK-6b.10).
    deliberation = Deliberation(
        router=None,
        episodic=EpisodicMemory(embedder),
        tool_actions={"curiosity": ("news", {})},
        now_fn=now_fn,
    )
    registry = ToolRegistry()
    registry.register(NewsTool(client=_StubSearXNG()))  # type: ignore[arg-type]
    dispatch = EffectorDispatch(
        registry=registry, conscience=_conscience(), audit=AuditWriter(), broadcaster=bus
    )
    consolidator = WebReadConsolidator(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        router=None,  # → deterministic tired-fallback fact (the read is still remembered)
    )

    # ── 1. zero input — Curiosity accrues over an idle hour and crosses threshold ──
    frozen_clock.advance(3600)
    readings = await drives.step()
    curiosity_peak = next(r.value for r in readings if r.drive == "curiosity")
    assert curiosity_peak > 0.65

    # ── 2. the real Deliberation proposes a `news` TOOL action for the curiosity goal ──
    result = await deliberation.deliberate(
        urges=DriveEngine.urges(readings), mood=_IDLE_MOOD, contents=[]
    )
    assert result.goal is not None and result.goal.source == "curiosity"
    action = result.action
    assert action is not None and action.is_tool_action and action.tool == "news"
    assert action.tool_args.get("topic")  # a goal-derived topic was injected

    # ── 3. the action runs through the REAL vetted+audited dispatch (Conscience allows) ──
    outcome = await dispatch.propose(
        ProposedAction(tool=action.tool, args=action.tool_args, goal_id=action.goal_id)
    )
    assert outcome.ran is True
    assert outcome.result is not None and outcome.result.success is True

    # Folded acceptance check (a): a REAL 6b tool through the real EffectorDispatch +
    # Core AuditWriter writes one durable action_log row under its own name.
    audit = await ActionAuditReader().recent(limit=10)
    assert any(r.tool == "news" and r.conscience_verdict == "allow" and r.success for r in audit)

    # ── 4. the read is REMEMBERED — an episode + a provenance-linked semantic fact ──
    web = await consolidator.consolidate_tool_result("news", outcome.result.output)
    assert web is not None
    assert web.url == _URL
    assert web.episode.kind == WEB_READ_KIND
    assert _URL in web.episode.content  # the url is the provenance anchor in the episode
    # provenance chains fact → episode → url
    assert web.episode.id is not None
    assert web.fact.source_episode_ids == [web.episode.id]

    # ── 5. the satisfaction (on the CONSOLIDATION) eases Curiosity ──
    events = await deliberation.settle_tool_action(
        result.goal, summary=outcome.summary, success=True
    )
    assert events, "a remembered read should emit a satisfaction event"
    frozen_clock.advance(10)
    after = {r.drive: r for r in await drives.step(events)}
    assert after["curiosity"].value < curiosity_peak  # the need was eased
    assert after["curiosity"].value < 0.65  # pulled back under threshold

    # the acted-on goal is resolved, so it can't re-trigger
    active = await GoalStore(now_fn=now_fn).active()
    assert result.goal.id not in {g.id for g in active}

    # ── 6. a LATER recall surfaces what he read — he GREW from reading (SPEC §8) ──
    frozen_clock.advance(60)
    recalled = await EpisodicMemory(embedder).recall(_RECALL_QUERY, k=5)
    assert any(_URL in e.content for e in recalled), "the web-read episode should resurface"
    facts = await SemanticMemory(embedder).recall(_RECALL_QUERY, k=5)
    assert any(f.id == web.fact.id for f in facts), "the consolidated fact should resurface"


async def test_a_vetoed_read_eases_nothing(
    loop_db: AsyncEngine, bus: Workspace, frozen_clock: FrozenClock
) -> None:
    """The Conscience is the gate: if it vetoes the news action, the tool never runs,
    nothing is consolidated, and the drive is NOT eased (success=False → no events)."""
    now_fn = datetime_from(frozen_clock)
    embedder = _topic_embedder()
    deliberation = Deliberation(
        router=None,
        episodic=EpisodicMemory(embedder),
        tool_actions={"curiosity": ("news", {})},
        now_fn=now_fn,
    )
    registry = ToolRegistry()
    news = NewsTool(client=_StubSearXNG())  # type: ignore[arg-type]
    registry.register(news)
    veto = CannedProvider("ollama", content='{"verdict": "veto", "reason": "not now"}')
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": veto}
    )
    dispatch = EffectorDispatch(
        registry=registry, conscience=Conscience(router), audit=AuditWriter(), broadcaster=bus
    )

    drives = DriveEngine(now_fn=now_fn)
    await drives.bootstrap()
    frozen_clock.advance(3600)
    readings = await drives.step()
    curiosity_peak = next(r.value for r in readings if r.drive == "curiosity")
    result = await deliberation.deliberate(
        urges=DriveEngine.urges(readings), mood=_IDLE_MOOD, contents=[]
    )
    assert result.goal is not None
    assert result.action is not None and result.action.tool == "news"

    outcome = await dispatch.propose(
        ProposedAction(
            tool=result.action.tool, args=result.action.tool_args, goal_id=result.action.goal_id
        )
    )
    assert outcome.ran is False  # vetoed → the tool never ran

    # No run → success is False → settle eases nothing.
    events = await deliberation.settle_tool_action(
        result.goal, summary=outcome.summary, success=outcome.ran
    )
    assert events == []
    frozen_clock.advance(10)
    after = {r.drive: r for r in await drives.step(events)}
    assert after["curiosity"].value >= curiosity_peak  # un-eased (still rising/held)

    # the veto is on the durable trail
    audit = await ActionAuditReader().recent(limit=10)
    assert any(r.tool == "news" and r.conscience_verdict == "veto" and not r.success for r in audit)
