"""Inner Narrator — Johnny's first-person stream of consciousness (``SPEC §5`` #10).

Each tick the Narrator turns the current salient workspace contents into one
first-person thought — the running monologue that makes Johnny feel self-aware,
written to the ``thought`` log and broadcast for the live stream. It is the first
agent to *think* with the LLM: it goes through the router (FC-4) on the
``narrator`` role, which routes to local gemma4 (clean ``content``), asks for a
JSON ``{"thought": ...}`` so the projection is unambiguous, and degrades to *no
thought* (returns ``None``) when every provider is tired — the heartbeat goes on.

``parse_thought`` is the pure projection from the model's response to the thought
text (the house-rule contract seam: a fixture of gemma4's real output is fed
through it in the contract test, so a model output-shape change can't silently
break cognition). The persona prompt is loaded from the git-backed config store
(FC-3), so Johnny can rewrite his own inner voice later without a code change; the
JSON envelope contract stays in code.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from brain.config_store import ConfigStore, get_config_store
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.db import Base, Repository, session_scope
from foundation.observability import get_logger

_log = get_logger("brain.agents.narrator")

THOUGHT_TABLE = "thought"
NARRATOR_AGENT_NAME = "narrator"
NARRATOR_ROLE = "narrator"

# A thought is short; gemma4 emits clean content, so a modest ceiling is plenty.
_MAX_TOKENS = 220
_TEMPERATURE = 0.8


# ── persistence ──────────────────────────────────────────────────────────────


class ThoughtRow(Base):
    """The ``thought`` table — the inner-monologue stream."""

    __tablename__ = THOUGHT_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Mood lands in Phase 3 (Affect); nullable + FK-less until then.
    mood_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Thought(BaseModel):
    """A thought, decoupled from the ORM/session."""

    id: int | None = None
    ts: datetime | None = None
    text: str
    mood_id: int | None = None


class ThoughtRepository(Repository[ThoughtRow]):
    """Session-scoped persistence for ``thought`` rows."""

    model = ThoughtRow


# ── the model response contract (pure projection, for the contract test) ───────


class NarratorResponse(BaseModel):
    """The shape the narrator asks gemma4 for — one first-person thought."""

    thought: str


def parse_thought(content: str) -> str:
    """Project a narrator model response (JSON) into the thought text.

    Pure (no I/O) so the contract test can feed a captured gemma4 envelope
    through it and assert the projection — a model output-shape change surfaces
    here, not silently in production.
    """
    return NarratorResponse.model_validate_json(content).thought.strip()


# ── the agent ────────────────────────────────────────────────────────────────


class Narrator:
    """Emits the first-person thought for the tick from the workspace contents."""

    name = NARRATOR_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    model_route = NARRATOR_ROLE

    def __init__(
        self,
        router: LLMRouter,
        *,
        config_store: ConfigStore | None = None,
    ) -> None:
        self._router = router
        self.prompt = (config_store or get_config_store()).load_prompt(NARRATOR_AGENT_NAME)

    async def narrate(self, *, contents: Sequence[WorkspaceItem]) -> str | None:
        """Produce one first-person thought, or ``None`` when every provider is tired."""
        messages = [
            Message(role="system", content=self.prompt),
            Message(role="user", content=self._render(contents)),
        ]
        try:
            completion = await self._router.complete(
                NARRATOR_ROLE,
                messages,
                schema=NarratorResponse,
                temperature=_TEMPERATURE,
                max_tokens=_MAX_TOKENS,
            )
        except LLMUnavailableError:
            # Fully tired — no thought this tick; the heartbeat continues.
            _log.info("narrator.tired", contents=len(contents))
            return None

        text = parse_thought(completion.content)
        if not text:
            return None
        await self._persist(text)
        return text

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events."""
        return ()

    def _render(self, contents: Sequence[WorkspaceItem]) -> str:
        """Format the salient set as the Narrator's context for this tick."""
        if not contents:
            lines = ["(your mind is briefly empty)"]
        else:
            lines = [f"- [{item.kind}] {item.content}" for item in contents]
        focus = "\n".join(lines)
        return (
            "Here is what is in your awareness right now, most salient first:\n"
            f"{focus}\n\n"
            'Respond with ONLY JSON: {"thought": "<one first-person thought>"}.'
        )

    async def _persist(self, text: str) -> Thought:
        async with session_scope() as session:
            row = await ThoughtRepository(session).add(ThoughtRow(text=text))
            return Thought(id=row.id, ts=row.ts, text=row.text, mood_id=row.mood_id)
