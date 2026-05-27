"""``memory_write`` + ``memory_search`` — Johnny's own memory, as explicit tools.

Phase 1–4 gave Johnny memory the *cycle* writes and recalls automatically (each
tick learns an episode, recall surfaces relevant ones). 6b also lets him reach his
memory **deliberately**, as a tool action a goal can select: ``memory_write`` to
record something on purpose, ``memory_search`` to go looking. Both are
``danger:safe`` (they touch only his own stores) and — being tools on 6a's belt —
run only through the vetted + audited dispatch.

They are thin wrappers over the existing memory spine (FC-4: never re-implement
storage): ``memory_write`` appends an episode to the autobiography;
``memory_search`` blends episodic recall (similarity × recency × salience) with
semantic-fact recall, so a search returns both lived moments and consolidated
knowledge, each ranked. The ``EpisodicMemory``/``SemanticMemory`` deps are injected
(real by default) so the composition root wires them and tests inject deterministic
embedders.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from brain.effectors.tools import DangerClass, ToolResult
from brain.memory.episodic import Episode, EpisodicMemory
from brain.memory.semantic import SemanticMemory

MEMORY_WRITE_TOOL_NAME = "memory_write"
MEMORY_SEARCH_TOOL_NAME = "memory_search"

# The kind stamped on an episode Johnny chooses to remember (vs the cycle's
# auto-logged percepts/thoughts) — so a deliberate memory is distinguishable.
_DELIBERATE_KIND = "self_memory"
# Bounds on a search so a single tool action can't pull an unbounded slab into the
# workspace (Attention is a bottleneck, SPEC §5).
_MAX_SEARCH_K = 20


class MemoryWriteArgs(BaseModel):
    """Args for ``memory_write``: the ``content`` to remember (+ optional kind/salience)."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    kind: str = Field(default=_DELIBERATE_KIND, min_length=1, max_length=64)
    salience: float = Field(default=0.6, ge=0.0, le=1.0)


class MemoryWriteTool:
    """Append a chosen memory to the episodic autobiography (``danger:safe``)."""

    name = MEMORY_WRITE_TOOL_NAME
    danger = DangerClass.SAFE
    args_schema = MemoryWriteArgs

    def __init__(self, *, episodic: EpisodicMemory | None = None) -> None:
        self._episodic = episodic or EpisodicMemory()

    async def run(self, args: MemoryWriteArgs) -> ToolResult:
        episode = await self._episodic.write(
            Episode(kind=args.kind, content=args.content, salience=args.salience)
        )
        return ToolResult(
            success=True,
            output={"id": episode.id, "kind": episode.kind},
            summary=f"remembered: {args.content[:80]}",
        )


class MemorySearchArgs(BaseModel):
    """Args for ``memory_search``: a ``query`` and how many results ``k`` to surface."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=_MAX_SEARCH_K)


class MemorySearchTool:
    """Search Johnny's own memory — episodic recall + semantic facts (``danger:safe``)."""

    name = MEMORY_SEARCH_TOOL_NAME
    danger = DangerClass.SAFE
    args_schema = MemorySearchArgs

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
    ) -> None:
        self._episodic = episodic or EpisodicMemory()
        self._semantic = semantic or SemanticMemory()

    async def run(self, args: MemorySearchArgs) -> ToolResult:
        episodes = await self._episodic.recall(args.query, k=args.k)
        facts = await self._semantic.recall(args.query, k=args.k)
        episode_payload = [
            {
                "id": e.id,
                "kind": e.kind,
                "content": e.content,
                "score": round(e.score, 4) if e.score is not None else None,
            }
            for e in episodes
        ]
        fact_payload = [
            {
                "id": f.id,
                "subject": f.subject,
                "predicate": f.predicate,
                "object": f.object,
                "score": round(f.score, 4) if f.score is not None else None,
            }
            for f in facts
        ]
        return ToolResult(
            success=True,
            output={"episodes": episode_payload, "facts": fact_payload},
            summary=(
                f"recalled {len(episode_payload)} episode(s) + "
                f"{len(fact_payload)} fact(s) for {args.query!r}"
            ),
        )
