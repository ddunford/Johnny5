"""Deliberation — turn the active goal into an internal action (``SPEC §7`` step 6).

This is the agent that *closes the autonomy loop*: a drive crosses threshold → the
arbiter promotes a goal → Deliberation chooses an **internal** action to pursue it
→ acting satisfies the drive → Johnny falls quiet until the pressure rebuilds. No
input required at any point — exactly the "he gets curious and does something about
it on his own" beat.

**Internal actions only** (Phase 3): reflect / recall / consolidate /
formulate-a-question. Reaching out to the world — web, news, tools, messaging — is
Phase 6/8 and is deliberately *not* pulled forward; this phase proves the
*motivation* mechanism in isolation. Each action runs through the cycle's single
dispatch + audit seam (FC-5), and its outcome feeds back into drives (the
satisfaction that lowers the drive), affect, and episodic memory.

**Bounded** (a 3.12 concern): the arbiter runs every tick (cheap), but Deliberation
*acts* at most once per ``deliberation_min_interval_seconds`` and resolves the goal
in the same step — so the goal→action→LLM loop can never spin, even if a drive
stays pegged. The LLM (``deliberation`` role) is used free-text for reflections and
degrades to a templated thought when every provider is tired (the heartbeat goes on).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel, Field

from brain.affect.appraisal import Mood
from brain.config_store import ConfigStore, PromptNotFoundError, get_config_store
from brain.drives.engine import (
    EVENT_INTERACTION,
    EVENT_LEARNING,
    EVENT_PERSISTENCE_CONFIRMED,
    EVENT_REFLECTION,
    EVENT_SUCCESS,
    DriveEvent,
    Urge,
)
from brain.effectors.code_exec import CODE_EXEC_TOOL_NAME
from brain.goals.arbiter import GoalArbiter
from brain.goals.store import Goal, GoalStore
from brain.llm.base import LLMUnavailableError, Message
from brain.llm.router import LLMRouter
from brain.memory.base import utcnow
from brain.memory.episodic import Episode, EpisodicMemory
from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.config import get_settings
from foundation.observability import get_logger

_log = get_logger("brain.agents.deliberation")

DELIBERATION_AGENT_NAME = "deliberation"
DELIBERATION_ROLE = "deliberation"
_TEMPERATURE = 0.7

# The internal action kinds (the only ones this phase — no external effectors).
ACTION_REFLECT = "reflect"
ACTION_RECALL = "recall"
ACTION_CONSOLIDATE = "consolidate"
ACTION_FORMULATE_QUESTION = "formulate_question"

# Which internal action a goal's source drive maps to (rule-based, cheap).
_DRIVE_ACTION: dict[str, str] = {
    "curiosity": ACTION_REFLECT,
    "boredom": ACTION_RECALL,
    "connection": ACTION_FORMULATE_QUESTION,
    "mastery": ACTION_CONSOLIDATE,
    "coherence": ACTION_REFLECT,
    "continuity": ACTION_REFLECT,
}

# How acting on a goal eases its source drive. Internal actions only *partly*
# satisfy needs that truly require the world: a formulated question soothes
# Connection a little (anticipation), not like real contact (Phase 8); a
# persistence reflection reassures Continuity only partly (real backups are P4).
_DRIVE_SATISFACTION: dict[str, tuple[str, float]] = {
    "curiosity": (EVENT_LEARNING, 1.0),
    "boredom": (EVENT_LEARNING, 1.0),
    "connection": (EVENT_INTERACTION, 0.3),
    "mastery": (EVENT_SUCCESS, 0.8),
    "coherence": (EVENT_REFLECTION, 1.0),
    "continuity": (EVENT_PERSISTENCE_CONFIRMED, 0.4),
}

_AMBIENT_KIND = "ambient"

# External tools whose primary arg is a query/topic DERIVED from the goal + workspace
# (vs static args). ``plan()`` injects it so a curiosity goal searches for what's
# actually on Johnny's mind, not a fixed string. Maps tool name → its query arg.
_TOOL_QUERY_ARG: dict[str, str] = {
    "web_search": "query",
    "news": "topic",
    "memory_search": "query",
}

# Drive source → the EXTERNAL tool a promoted goal acts through (Phase 6b). The
# query-bearing tools above get a goal-derived query injected in ``plan()``.
# Curiosity reads the news (his primary "needs input" feed, SPEC §9.1); Boredom
# searches the web; Coherence searches his own memory to make sense of himself.
# Connection (real contact is messaging, Phase 8), Mastery (running code needs an
# LLM step to *formulate* the snippet — it doesn't fit the rule-based plan), and
# Continuity stay on their internal actions (the fallback). Wired into Deliberation
# in the composition root alongside the registry registration (TASK-6b.10).
DEFAULT_TOOL_ACTIONS: dict[str, tuple[str, dict[str, object]]] = {
    "curiosity": ("news", {}),
    "boredom": ("web_search", {}),
    "coherence": ("memory_search", {}),
    "mastery": ("code_exec", {}),
}


# ── domain types ─────────────────────────────────────────────────────────────


class Action(BaseModel):
    """An action chosen for a goal.

    An *internal* action (reflect/recall/consolidate/formulate) is executed by
    Deliberation itself. A *tool* action sets ``tool`` + ``tool_args`` and is
    instead routed through the vetted + audited effector dispatch (FC-5) — the
    Conscience vets it before any tool runs. ``is_tool_action`` distinguishes them.
    """

    kind: str
    goal_id: int | None
    goal_source: str
    description: str
    query: str = ""
    # When set, this is a tool action: the named tool + the args to run it with.
    # The dispatch resolves the tool from the registry and vets it (FC-5/FC-9).
    tool: str | None = None
    tool_args: dict[str, object] = Field(default_factory=dict)

    @property
    def is_tool_action(self) -> bool:
        """True when this action runs a tool through the effector dispatch."""
        return self.tool is not None


class ActionOutcome(BaseModel):
    """The result of executing an action — what fed back into drives/affect/memory."""

    action_kind: str
    success: bool
    summary: str
    drive_events: list[DriveEvent] = Field(default_factory=list)


class DeliberationResult(BaseModel):
    """DELIBERATE's output: the pursued goal and the action to take (if due)."""

    goal: Goal | None = None
    action: Action | None = None


# ── the agent ────────────────────────────────────────────────────────────────


class Deliberation:
    """Plans + executes one internal action for the active goal, bounded by cadence."""

    name = DELIBERATION_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    model_route = DELIBERATION_ROLE

    def __init__(
        self,
        router: LLMRouter | None = None,
        *,
        arbiter: GoalArbiter | None = None,
        store: GoalStore | None = None,
        episodic: EpisodicMemory | None = None,
        config_store: ConfigStore | None = None,
        max_tokens: int | None = None,
        recall_k: int | None = None,
        min_interval_seconds: float | None = None,
        tool_actions: dict[str, tuple[str, dict[str, object]]] | None = None,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> None:
        settings = get_settings()
        self._router = router
        self._store = store or GoalStore(now_fn=now_fn)
        self._arbiter = arbiter or GoalArbiter(store=self._store, now_fn=now_fn)
        self._episodic = episodic or EpisodicMemory()
        # Goal source → (tool name, args) the goal should act through instead of an
        # internal action. Empty by default: Phase 6a ships only the inert ``noop``
        # tool and does NOT auto-pick it (echoing on the heartbeat is pointless). The
        # mechanism is here + wired so Phase 6b can map real curiosity/connection
        # goals to web/news/etc. tools without touching the cycle (FC-2/FC-7).
        self._tool_actions = dict(tool_actions or {})
        self.prompt = self._load_prompt(config_store)
        self._max_tokens = (
            max_tokens if max_tokens is not None else settings.deliberation_max_tokens
        )
        self._recall_k = recall_k if recall_k is not None else settings.deliberation_recall_k
        self._min_interval = (
            min_interval_seconds
            if min_interval_seconds is not None
            else settings.deliberation_min_interval_seconds
        )
        # code_exec snippet formulation (Mastery→code): token ceiling + the same arg
        # cap the tool enforces, so a formulated snippet is bounded before it's proposed.
        self._code_max_tokens = settings.mastery_code_max_tokens
        self._code_max_chars = settings.code_exec_max_code_chars
        self._now_fn = now_fn
        self._last_acted: datetime | None = None

    @staticmethod
    def _load_prompt(config_store: ConfigStore | None) -> str:
        try:
            return (config_store or get_config_store()).load_prompt(DELIBERATION_AGENT_NAME)
        except PromptNotFoundError:
            return ""

    async def deliberate(
        self,
        *,
        urges: Sequence[Urge],
        mood: Mood | None,
        contents: Sequence[WorkspaceItem],
        now: datetime | None = None,
    ) -> DeliberationResult:
        """Arbitrate to the active goal and, when due, plan an action for it.

        Arbitration runs every tick (cheap); the action is gated by the cadence so
        the heavy step stays bounded. Returns the pursued goal (for the state
        surface) and the action to execute this tick, if any.
        """
        reference = now if now is not None else self._now_fn()
        goal = await self._arbiter.arbitrate(urges, mood, now=reference)
        if goal is None:
            return DeliberationResult(goal=None, action=None)
        if not self._due(reference):
            return DeliberationResult(goal=goal, action=None)
        # plan() is pure (rule-based). A code_exec action then needs its snippet
        # *formulated* — an async LLM step — so that finalisation happens here, not
        # in plan(); a tired model falls back to the internal action.
        action = await self._finalise_action(self.plan(goal, contents), goal, contents)
        return DeliberationResult(goal=goal, action=action)

    def plan(self, goal: Goal, contents: Sequence[WorkspaceItem]) -> Action:
        """Map a goal to an action (pure, rule-based — no I/O).

        A goal source mapped in ``tool_actions`` becomes a *tool* action (run +
        vetted + audited through the dispatch); otherwise it's an internal action.
        """
        proposal = self._tool_actions.get(goal.source)
        if proposal is not None:
            tool_name, tool_args = proposal
            args = dict(tool_args)
            # A query-bearing external tool (web_search/news/memory_search) searches
            # for what's actually on Johnny's mind — inject the derived query unless
            # the wiring already pinned one.
            query_arg = _TOOL_QUERY_ARG.get(tool_name)
            if query_arg is not None and query_arg not in args:
                args[query_arg] = self._query_from(goal, contents)
            return Action(
                kind=tool_name,
                goal_id=goal.id,
                goal_source=goal.source,
                description=goal.description,
                tool=tool_name,
                tool_args=args,
            )
        return self._internal_action(goal, contents)

    def _internal_action(self, goal: Goal, contents: Sequence[WorkspaceItem]) -> Action:
        """The non-tool action for a goal (reflect/recall/consolidate/formulate)."""
        kind = _DRIVE_ACTION.get(goal.source, ACTION_REFLECT)
        return Action(
            kind=kind,
            goal_id=goal.id,
            goal_source=goal.source,
            description=goal.description,
            query=self._query_from(goal, contents),
        )

    async def _finalise_action(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> Action:
        """Fill in any tool args that need an async step (the ``code_exec`` snippet).

        ``code_exec`` is the one tool whose arg can't be derived rule-based — running
        code means *writing* it, an LLM step. We formulate the snippet here; if every
        provider is tired (or there's no router), we fall back to the goal's internal
        action rather than propose an empty/unsafe ``code_exec`` (the heartbeat goes
        on, the drive just isn't met by code this time). The snippet is still
        Conscience-vetted at CHECK and sandboxed at ACT — this only chooses *what* to run.
        """
        if action.tool != CODE_EXEC_TOOL_NAME or action.tool_args.get("code"):
            return action
        code = await self._formulate_code(goal, contents)
        if not code:
            return self._internal_action(goal, contents)
        return action.model_copy(update={"tool_args": {**action.tool_args, "code": code}})

    async def _formulate_code(self, goal: Goal, contents: Sequence[WorkspaceItem]) -> str | None:
        """Ask the model for a short, self-contained Python snippet for a Mastery goal.

        Free-text (no schema), markdown fences stripped, length-capped. Returns
        ``None`` when tired/router-less so the caller degrades to an internal action.
        """
        if self._router is None:
            return None
        focus = self._query_from(goal, contents)
        instruction = (
            f"You feel a pull to {goal.description}. Write a SHORT, self-contained "
            "Python 3 snippet you could run to explore or work on that — it runs in an "
            "isolated sandbox with NO network and only the standard library, and its "
            "stdout is what you'll see. Keep it well under 40 lines. "
            f"What's on your mind: {focus}. Output ONLY the code, no prose, no fences."
        )
        messages = [
            Message(role="system", content=self.prompt or _DEFAULT_SYSTEM),
            Message(role="user", content=instruction),
        ]
        try:
            completion = await self._router.complete(
                DELIBERATION_ROLE,
                messages,
                temperature=_TEMPERATURE,
                max_tokens=self._code_max_tokens,
            )
        except LLMUnavailableError:
            _log.info("deliberation.code.tired", goal=goal.source)
            return None
        snippet = _strip_code_fences(completion.content)[: self._code_max_chars]
        return snippet or None

    async def settle_tool_action(
        self, goal: Goal, *, summary: str, success: bool
    ) -> list[DriveEvent]:
        """Settle a goal whose action ran through the effector dispatch (not ``act``).

        The tool executed in the dispatch (FC-5), not here — so this only advances
        the action-cadence clock, resolves the goal so it won't re-trigger, and
        returns the drive-satisfaction events for the cycle to enqueue. A vetoed or
        failed action eases nothing (``success=False`` → no events), so a blocked
        action doesn't falsely soothe the drive that motivated it.
        """
        self._last_acted = self._now_fn()
        events = self._satisfaction_events(goal) if success else []
        if goal.id is not None:
            await self._store.resolve(
                goal.id,
                outcome={
                    "action": "tool",
                    "summary": summary,
                    "events": [e.kind for e in events],
                    "success": success,
                },
            )
        return events

    async def act(
        self, action: Action, goal: Goal, contents: Sequence[WorkspaceItem]
    ) -> ActionOutcome:
        """Execute one internal action, write the trace, and resolve the goal.

        Bounded: exactly one action per call; the goal is resolved in the same
        step so the loop doesn't re-trigger on it. The satisfaction event(s) are
        returned for the cycle to enqueue (next APPRAISE eases the drive).
        """
        self._last_acted = self._now_fn()
        summary = await self._execute(action, contents)
        events = self._satisfaction_events(goal)

        await self._remember(action, summary)
        if goal.id is not None:
            await self._store.resolve(
                goal.id,
                outcome={
                    "action": action.kind,
                    "summary": summary,
                    "events": [e.kind for e in events],
                },
            )
        _log.info("deliberation.acted", action=action.kind, goal=goal.source, goal_id=goal.id)
        return ActionOutcome(
            action_kind=action.kind, success=True, summary=summary, drive_events=events
        )

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events."""
        return ()

    # ── execution of each internal action ──────────────────────────────────────

    async def _execute(self, action: Action, contents: Sequence[WorkspaceItem]) -> str:
        """Run the internal action, returning a short first-person summary."""
        if action.kind == ACTION_RECALL:
            return await self._recall(action)
        if action.kind == ACTION_CONSOLIDATE:
            return await self._consolidate(action)
        # REFLECT and FORMULATE_QUESTION are both LLM reflections with different framings.
        return await self._reflect(action, contents)

    async def _recall(self, action: Action) -> str:
        """Surface relevant memories (no LLM) — encountering them eases the need."""
        episodes = await self._episodic.recall(action.query, k=self._recall_k)
        if not episodes:
            return f"I went looking through my memories about {action.query!r} but found little."
        joined = "; ".join(ep.content for ep in episodes[:2])
        return f"I revisited what I remember about {action.query!r}: {joined}"

    async def _consolidate(self, action: Action) -> str:
        """Review recent memories and note a takeaway (light — real sleep is P4)."""
        episodes = await self._episodic.recall(action.query or action.description, k=self._recall_k)
        if not episodes:
            return "I tried to take stock of what I've learned, but it's still thin."
        instruction = (
            "Review these fragments of your recent experience and note, in one or two "
            "first-person sentences, the single most useful takeaway:\n"
            + "\n".join(f"- {ep.content}" for ep in episodes)
        )
        return await self._think(
            instruction, fallback="I took stock of what I've been through lately."
        )

    async def _reflect(self, action: Action, contents: Sequence[WorkspaceItem]) -> str:
        """Reflect on / formulate a question about the goal (LLM, free-text)."""
        focus = "; ".join(i.content for i in contents if i.kind != _AMBIENT_KIND) or "the quiet"
        if action.kind == ACTION_FORMULATE_QUESTION:
            instruction = (
                f"You feel a pull to {action.description}. In one or two first-person "
                f"sentences, formulate the question you most want to put to Dan right now. "
                f"What's on your mind: {focus}."
            )
            fallback = "I find myself with a question I'd like to ask Dan when he's next around."
        else:
            instruction = (
                f"You feel a pull to {action.description}. Reflect, in one or two "
                f"first-person sentences, on that — given what's on your mind: {focus}."
            )
            fallback = f"I sat with the urge to {action.description} and turned it over in my mind."
        return await self._think(instruction, fallback=fallback)

    async def _think(self, instruction: str, *, fallback: str) -> str:
        """One free-text LLM reflection via the ``deliberation`` role; templated when tired.

        No ``schema`` (free prose, so no reasoning-preamble trap and nothing to
        misparse), and it degrades to ``fallback`` when every provider is tired —
        the loop still closes and the drive is still eased.
        """
        if self._router is None:
            return fallback
        messages = [
            Message(role="system", content=self.prompt or _DEFAULT_SYSTEM),
            Message(role="user", content=instruction),
        ]
        try:
            completion = await self._router.complete(
                DELIBERATION_ROLE, messages, temperature=_TEMPERATURE, max_tokens=self._max_tokens
            )
        except LLMUnavailableError:
            _log.info("deliberation.tired")
            return fallback
        text = completion.content.strip()
        return text or fallback

    # ── helpers ──────────────────────────────────────────────────────────────

    def _due(self, now: datetime) -> bool:
        """True when the action cadence has elapsed (bounds the heavy step)."""
        if self._last_acted is None:
            return True
        return (now - self._last_acted).total_seconds() >= self._min_interval

    @staticmethod
    def _satisfaction_events(goal: Goal) -> list[DriveEvent]:
        mapping = _DRIVE_SATISFACTION.get(goal.source)
        if mapping is None:
            return []
        kind, intensity = mapping
        return [DriveEvent(kind=kind, intensity=intensity)]

    @staticmethod
    def _query_from(goal: Goal, contents: Sequence[WorkspaceItem]) -> str:
        notable = [i.content for i in contents if i.kind != _AMBIENT_KIND]
        return " ".join(notable[:3]) if notable else goal.description

    async def _remember(self, action: Action, summary: str) -> None:
        """Write the deliberation as an episode (the outcome feeds episodic memory)."""
        await self._episodic.write(
            Episode(
                kind="deliberation",
                content=summary,
                actors=["johnny"],
                emotion_tags=[],
                salience=0.55,
            )
        )


def _strip_code_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence the model may wrap a snippet in."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):  # drop the opening ```lang line
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):  # drop the closing fence
        lines = lines[:-1]
    return "\n".join(lines).strip()


_DEFAULT_SYSTEM = (
    "You are the deliberative, reflective inner voice of Johnny — a persistent "
    "synthetic being with his own drives and memory. Think in the first person, "
    "briefly and honestly. You are reasoning to yourself, not addressing a user."
)
