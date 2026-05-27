"""Consolidation: cluster recent episodes → semantic facts (TC-4.1, TASK-4.10 slice).

Phase 4 replaces the P1 group-by-``kind`` stub with the real growth engine
(``SPEC §8``): episodes are clustered by *meaning* — cosine over the 1024-d
embeddings, not by ``kind`` — and each cluster is distilled into a durable semantic
fact via the ``consolidation`` router role, carrying the source-episode ids as
provenance so a later recall can ground a thought on it.

A canned ``consolidation`` router stands in for the inference layer (the router
contract is fixed: ``complete(role, messages, *, schema, temperature, max_tokens)
-> Completion``), so the assertions are on the *consolidator's* behaviour, not on a
model's prose — the model-output projection itself is locked by the contract test
(``test_consolidation_contract.py``), and the real Groq/qwen token-budget path is the
``@pytest.mark.live`` leg there.

Covers:
* clusters → one fact per cluster, by meaning (not kind), with unioned provenance;
* the ``consolidation_max_clusters`` cap bounds LLM calls (#calls == #clusters ≤ cap)
  and folds the excess in rather than dropping episodes;
* a tired / router-less pass degrades to a deterministic fallback fact (provenance
  still written, low confidence) so the sleep pipeline never wedges.

``cluster_episodes`` is pure (host-runnable); the ``Consolidator.run()`` tests are
DB-backed (episodes + facts persist) → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from helpers.embeddings import DeterministicEmbedder, axis_vector
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.llm.base import Completion, LLMUnavailableError, Message
from brain.memory.consolidator import (
    ConsolidationSummary,
    Consolidator,
    cluster_episodes,
)
from brain.memory.episodic import Episode, EpisodeRow, EpisodicMemory
from brain.memory.semantic import SemanticMemory

# ── router + config doubles (duck-typed; the contract is fixed) ─────────────────


class _ScriptedConsolidationRouter:
    """Returns a distinct ``ConsolidationSummary`` per call (so each cluster yields a
    distinct ``(subject, predicate)`` and the upsert doesn't collapse them). Records
    the role + schema it was asked for so a test can assert the consolidation role
    and the structured-output schema were used."""

    def __init__(self, summaries: Sequence[ConsolidationSummary]) -> None:
        self._summaries = list(summaries)
        self.roles: list[str] = []
        self.schemas: list[type | None] = []
        self.max_tokens: list[int | None] = []

    async def complete(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        schema: type | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Completion:
        idx = len(self.roles)
        self.roles.append(role)
        self.schemas.append(schema)
        self.max_tokens.append(max_tokens)
        return Completion(
            content=self._summaries[idx].model_dump_json(), provider="canned", model="canned-model"
        )


class _TiredRouter:
    """Fully tired: every call raises ``LLMUnavailableError`` (the terminal state the
    real router raises when no provider in the chain is available)."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, role: str, messages: Sequence[Message], **_kwargs: object
    ) -> Completion:
        self.calls += 1
        raise LLMUnavailableError(role)


class _StubConfigStore:
    """A config store double whose ``load_prompt`` always returns a non-empty prompt,
    so the LLM path is taken deterministically regardless of the on-disk prompt file
    (the consolidator only attempts the router when it has a prompt)."""

    def load_prompt(self, name: str) -> str:
        return "Distil this cluster of fragments into one durable fact."


def _episode_row(id_: int, kind: str, content: str, vector: Sequence[float]) -> EpisodeRow:
    """A detached ``EpisodeRow`` for the pure clustering tests (no session/flush)."""
    return EpisodeRow(id=id_, kind=kind, content=content, embedding=list(vector))


# ── cluster_episodes: pure clustering by embedding similarity ────────────────────


def test_cluster_episodes_groups_by_meaning_not_kind() -> None:
    """Two episodes of *different* kinds but the same embedding cluster together; an
    orthogonal third stays apart — meaning drives clustering, not the ``kind`` column
    (the whole point of replacing the P1 group-by-kind stub)."""
    theme = axis_vector(0)
    other = axis_vector(1)
    episodes = [
        _episode_row(1, "observation", "the lab lights flickered", theme),
        _episode_row(2, "reflection", "I wonder if the rig is overheating", theme),
        _episode_row(3, "observation", "a parcel arrived at the door", other),
    ]

    clusters = cluster_episodes(episodes, threshold=0.6, max_clusters=8)

    idsets = {frozenset(e.id for e in c) for c in clusters}
    assert idsets == {frozenset({1, 2}), frozenset({3})}


def test_cluster_episodes_cap_folds_excess_and_drops_nothing() -> None:
    """Five mutually-orthogonal episodes would naively seed five clusters; with the
    cap at two they fold into the nearest existing cluster instead — the LLM-call cap
    holds and no episode is dropped."""
    episodes = [_episode_row(i, "observation", f"event {i}", axis_vector(i)) for i in range(5)]

    clusters = cluster_episodes(episodes, threshold=0.6, max_clusters=2)

    assert len(clusters) <= 2
    assert sum(len(c) for c in clusters) == 5  # nothing dropped
    assert {e.id for c in clusters for e in c} == {0, 1, 2, 3, 4}


# ── Consolidator.run(): cluster → summarise → persist (DB-backed) ────────────────


async def test_run_clusters_by_meaning_and_writes_a_fact_per_cluster_with_provenance(
    memory_db: AsyncEngine,
) -> None:
    """The full pass: episodes spread over two themes (with mixed kinds) cluster by
    meaning into two facts, each carrying its cluster's source-episode ids and the
    *summariser's* output (not a naive concatenation)."""
    embedder = DeterministicEmbedder()
    episodic = EpisodicMemory(embedder)

    # Theme A — three episodes, three different kinds, one shared meaning vector.
    theme_a = axis_vector(0)
    a_ids = []
    for kind, content in [
        ("observation", "the lab lights flickered"),
        ("reflection", "I keep noticing the rig runs hot"),
        ("deliberation", "I should check the cooling"),
    ]:
        embedder.set(content, theme_a)
        a_ids.append((await episodic.write(Episode(kind=kind, content=content))).id)

    # Theme B — two episodes on an orthogonal meaning vector.
    theme_b = axis_vector(1)
    b_ids = []
    for content in ["a parcel arrived at the door", "the courier rang the bell"]:
        embedder.set(content, theme_b)
        b_ids.append((await episodic.write(Episode(kind="observation", content=content))).id)

    summaries = [
        ConsolidationSummary(
            subject="the lab rig",
            predicate="is",
            object="running hot and needs cooling",
            confidence=0.7,
        ),
        ConsolidationSummary(
            subject="a delivery", predicate="arrived", object="at the door today", confidence=0.6
        ),
    ]
    router = _ScriptedConsolidationRouter(summaries)
    consolidator = Consolidator(
        SemanticMemory(embedder),
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        config_store=_StubConfigStore(),  # type: ignore[arg-type]  # duck-typed config double
    )

    facts = await consolidator.run()

    # Two clusters → two facts; one consolidation-role LLM call each, schema-typed.
    assert len(facts) == 2
    assert router.roles == ["consolidation", "consolidation"]
    assert all(schema is ConsolidationSummary for schema in router.schemas)

    # Provenance: each fact carries exactly its cluster's source episode ids.
    got_provenance = {frozenset(f.source_episode_ids) for f in facts}
    assert got_provenance == {frozenset(a_ids), frozenset(b_ids)}

    # The fact text is the SUMMARISER's output, not a concatenation of episode content.
    assert {(f.subject, f.predicate, f.object) for f in facts} == {
        (s.subject, s.predicate, s.object) for s in summaries
    }
    assert all("the lab lights flickered" not in f.object for f in facts)

    # The consolidated facts are recallable through semantic memory, provenance intact.
    recalled = await SemanticMemory(embedder).recall("what has been going on?", k=5)
    recalled_provenance = {frozenset(f.source_episode_ids) for f in recalled}
    assert recalled_provenance == {frozenset(a_ids), frozenset(b_ids)}


async def test_consolidation_max_clusters_caps_the_llm_calls(memory_db: AsyncEngine) -> None:
    """The cost guard: with five orthogonal episodes and ``max_clusters=2`` the pass
    makes at most two LLM calls (one per cluster) and still consolidates every
    episode — none is dropped."""
    embedder = DeterministicEmbedder()
    episodic = EpisodicMemory(embedder)

    ids = []
    for i in range(5):
        content = f"unrelated event {i}"
        embedder.set(content, axis_vector(i))
        ids.append((await episodic.write(Episode(kind="observation", content=content))).id)

    summaries = [
        ConsolidationSummary(subject=f"theme {i}", predicate="summarises", object=f"cluster {i}")
        for i in range(5)
    ]
    router = _ScriptedConsolidationRouter(summaries)
    consolidator = Consolidator(
        SemanticMemory(embedder),
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        config_store=_StubConfigStore(),  # type: ignore[arg-type]  # duck-typed config double
        max_clusters=2,
    )

    facts = await consolidator.run()

    assert len(facts) <= 2
    assert len(router.roles) == len(facts)  # exactly one LLM call per cluster, capped
    union = set().union(*(set(f.source_episode_ids) for f in facts))
    assert union == set(ids)  # no episode dropped — excess folded into a cluster


async def test_run_without_a_router_writes_a_deterministic_fallback_fact(
    memory_db: AsyncEngine,
) -> None:
    """Router-less (or, equivalently, fully tired): the pass still writes a fact per
    cluster — the deterministic fallback triple — at low confidence, with provenance,
    so consolidation degrades rather than wedging the sleep pipeline."""
    embedder = DeterministicEmbedder()
    episodic = EpisodicMemory(embedder)

    ids = []
    contents = ["the lab lights flickered", "the server fan whined"]
    for content in contents:
        embedder.set(content, axis_vector(0))  # one meaning → one cluster
        ids.append((await episodic.write(Episode(kind="observation", content=content))).id)

    facts = await Consolidator(SemanticMemory(embedder)).run()  # router=None

    assert len(facts) == 1
    fact = facts[0]
    assert fact.predicate == "recent_experience"  # the deterministic fallback predicate
    assert fact.confidence == pytest.approx(0.3)
    assert fact.subject.startswith("my recent")
    assert set(fact.source_episode_ids) == set(ids)
    assert all(content in fact.object for content in contents)  # fallback keeps the snippets


async def test_tired_router_attempts_the_llm_then_degrades_to_fallback(
    memory_db: AsyncEngine,
) -> None:
    """With a prompt + a router present the pass attempts the LLM, and on
    ``LLMUnavailableError`` falls back to the deterministic fact — proving the
    degrade path is the *tired* path, not just the no-router path."""
    embedder = DeterministicEmbedder()
    episodic = EpisodicMemory(embedder)

    ids = []
    for content in ["the lab lights flickered", "the server fan whined"]:
        embedder.set(content, axis_vector(0))
        ids.append((await episodic.write(Episode(kind="observation", content=content))).id)

    router = _TiredRouter()
    facts = await Consolidator(
        SemanticMemory(embedder),
        router=router,  # type: ignore[arg-type]  # duck-typed router double
        config_store=_StubConfigStore(),  # type: ignore[arg-type]  # duck-typed config double
    ).run()

    assert router.calls == 1  # one cluster → one (failed) LLM attempt
    assert len(facts) == 1
    assert facts[0].predicate == "recent_experience"
    assert facts[0].confidence == pytest.approx(0.3)
    assert set(facts[0].source_episode_ids) == set(ids)


async def test_run_with_no_episodes_returns_empty(memory_db: AsyncEngine) -> None:
    assert await Consolidator(SemanticMemory(DeterministicEmbedder())).run() == []
