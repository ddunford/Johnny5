"""TC-2.5 — narration is memory-grounded (recall feeds cognition, not just storage).

The claim that distinguishes a memory *system* from a log: a seeded episode must
resurface through the RECALL stage into the workspace the Narrator reads, so the
thought reflects it. This drives the **real** ``MemoryRecaller`` (Phase-1 episodic
store) through the cycle; embeddings are injected (``DeterministicEmbedder``) so
recall is exact and reproducible — never the live TEI server. The Narrator is a
deterministic double that projects the workspace contents, so the assertion is on
the wiring (recall → blackboard → narrator input), not on a model's prose. The
real gemma4 narration is exercised separately under ``--run-live``.

DB+Redis backed → run in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from helpers.clock import FrozenClock
from helpers.cycle import PassthroughAttention, StubNarration, StubPerception, build_cycle
from helpers.embeddings import DeterministicEmbedder, axis_vector

from brain.agents.memory_stages import MemoryRecaller
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticMemory
from brain.workspace import Workspace, WorkspaceItem

# One on-topic axis: the resolver maps every text to it, so the seeded episode is
# an exact (cosine 1.0) match for whatever the recall query embeds to — recall is
# fully deterministic and the seeded memory always surfaces.
_TOPIC = axis_vector(0)


async def test_recalled_episode_surfaces_into_the_narrated_workspace(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    embedder = DeterministicEmbedder(resolver=lambda _text: _TOPIC)
    episodic = EpisodicMemory(embedder)
    seeded = await episodic.write(
        Episode(kind="memory", content="Dan prefers concise answers", salience=0.6)
    )

    recaller = MemoryRecaller(
        episodic=episodic,
        semantic=SemanticMemory(embedder),
        episodes_k=3,
        facts_k=0,  # no facts seeded; recall(k<=0) short-circuits without touching the store
    )
    narration = StubNarration()  # projects the workspace contents into the thought

    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(
            [WorkspaceItem(kind="input", content="what matters to Dan?", salience=0.9)]
        ),
        attention=PassthroughAttention(),
        recall=recaller,
        narration=narration,
    )

    report = (await harness.run_ticks(1))[0]

    # RECALL surfaced the seeded episode onto the blackboard the Narrator reads.
    contents = await workspace.contents()
    memory_items = [item for item in contents if item.kind == "memory"]
    assert "Dan prefers concise answers" in [item.content for item in memory_items]
    # Provenance is carried through (the episode id), proving it came from the store.
    assert memory_items[0].metadata.get("episode_id") == seeded.id
    # The embedder was used for the query (recall never embeds inline — FC-4).
    assert embedder.calls
    # And the thought reflects the recalled memory.
    assert report.thought is not None
    assert "Dan prefers concise answers" in report.thought


async def test_no_recall_when_nothing_relevant_is_seeded(
    workspace: Workspace, frozen_clock: FrozenClock
) -> None:
    """With an empty episodic store, recall surfaces nothing — the workspace holds
    only the live percept, so the grounding above is a real effect, not a fixture."""
    embedder = DeterministicEmbedder(resolver=lambda _text: _TOPIC)
    recaller = MemoryRecaller(
        episodic=EpisodicMemory(embedder),
        semantic=SemanticMemory(embedder),
        episodes_k=3,
        facts_k=0,
    )

    harness = build_cycle(
        workspace,
        clock=frozen_clock,
        perception=StubPerception(
            [WorkspaceItem(kind="input", content="what matters to Dan?", salience=0.9)]
        ),
        attention=PassthroughAttention(),
        recall=recaller,
        narration=StubNarration(),
    )

    await harness.run_ticks(1)

    contents = await workspace.contents()
    assert [item.kind for item in contents] == ["input"]
    assert not [item for item in contents if item.kind == "memory"]
