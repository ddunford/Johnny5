"""TC-6b.4 (planning half) — Deliberation maps a goal to an EXTERNAL tool action.

``plan()`` is pure (no I/O), so this is host-runnable. We prove the 6b extension:
a goal whose source is mapped to a query-bearing tool becomes a ``(tool, args)``
proposal with the query DERIVED from the goal + workspace (so Johnny searches for
what's actually on his mind), an unmapped drive falls back to an internal action,
and the shipped default mapping is what we expect. The vetting + run + consolidate
of that proposal is the cycle's job (proven elsewhere); here it's just selection.
"""

from __future__ import annotations

from collections.abc import Sequence

from brain.agents.deliberation import DEFAULT_TOOL_ACTIONS, Deliberation, _strip_code_fences
from brain.effectors.code_exec import CODE_EXEC_TOOL_NAME
from brain.goals.store import Goal
from brain.llm.base import Completion, Message
from brain.workspace import WorkspaceItem

_AMBIENT = WorkspaceItem(kind="ambient", content="all quiet", salience=0.1)
_THOUGHT = WorkspaceItem(kind="thought", content="I wonder about Mars rovers", salience=0.6)


def _deliberation() -> Deliberation:
    # router=None + no store/episodic touch: plan() is pure, so nothing is needed.
    return Deliberation(router=None, tool_actions=DEFAULT_TOOL_ACTIONS)


def test_curiosity_goal_plans_a_news_tool_action_with_derived_topic() -> None:
    goal = Goal(id=1, source="curiosity", description="learn something new", priority=0.7)
    action = _deliberation().plan(goal, [_AMBIENT, _THOUGHT])

    assert action.is_tool_action
    assert action.tool == "news"
    # The topic is derived from what's notable in the workspace (not the ambient line).
    assert action.tool_args["topic"] == "I wonder about Mars rovers"


def test_boredom_goal_plans_a_web_search_with_derived_query() -> None:
    goal = Goal(id=2, source="boredom", description="find something interesting", priority=0.6)
    action = _deliberation().plan(goal, [_THOUGHT])

    assert action.tool == "web_search"
    assert action.tool_args["query"] == "I wonder about Mars rovers"


def test_coherence_goal_plans_a_memory_search() -> None:
    goal = Goal(id=3, source="coherence", description="make sense of myself", priority=0.6)
    action = _deliberation().plan(goal, [])

    assert action.tool == "memory_search"
    # No notable workspace content → falls back to the goal description as the query.
    assert action.tool_args["query"] == "make sense of myself"


def test_unmapped_drive_falls_back_to_an_internal_action() -> None:
    # Continuity isn't mapped to an external tool — it stays internal (the fallback).
    goal = Goal(id=4, source="continuity", description="reassure myself I'll persist", priority=0.6)
    action = _deliberation().plan(goal, [_THOUGHT])

    assert not action.is_tool_action
    assert action.tool is None


def test_a_drive_with_no_mapping_when_map_is_empty_is_internal() -> None:
    # The 6a default (empty map) — no external tool is auto-picked.
    goal = Goal(id=5, source="curiosity", description="x", priority=0.6)
    action = Deliberation(router=None).plan(goal, [_THOUGHT])
    assert not action.is_tool_action


def test_default_mapping_is_the_expected_set() -> None:
    assert DEFAULT_TOOL_ACTIONS == {
        "curiosity": ("news", {}),
        "boredom": ("web_search", {}),
        "coherence": ("memory_search", {}),
        "mastery": ("code_exec", {}),
    }


# ── Mastery → code_exec: the LLM-formulated snippet (async finalisation) ───────


class _CodeRouter:
    """A router double returning a canned snippet (or raising) for code formulation."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0

    async def complete(self, role: str, messages: Sequence[Message], **_kw: object) -> Completion:
        self.calls += 1
        return Completion(content=self._content, provider="canned", model="canned")


async def test_mastery_goal_formulates_and_strips_a_code_snippet() -> None:
    router = _CodeRouter("```python\nprint(6 * 7)\n```")
    delib = Deliberation(router=router, tool_actions=DEFAULT_TOOL_ACTIONS)  # type: ignore[arg-type]
    goal = Goal(id=1, source="mastery", description="work a calculation", priority=0.7)

    # plan() builds the code_exec action with no code; _finalise_action formulates it.
    action = await delib._finalise_action(delib.plan(goal, []), goal, [])

    assert action.tool == CODE_EXEC_TOOL_NAME
    assert action.tool_args["code"] == "print(6 * 7)"  # markdown fences stripped
    assert router.calls == 1


async def test_mastery_falls_back_to_internal_when_tired() -> None:
    # No router → can't formulate code → degrade to the internal action, don't propose
    # an empty/unsafe code_exec.
    delib = Deliberation(router=None, tool_actions=DEFAULT_TOOL_ACTIONS)
    goal = Goal(id=2, source="mastery", description="practice", priority=0.6)

    action = await delib._finalise_action(delib.plan(goal, []), goal, [])

    assert not action.is_tool_action
    assert action.tool is None


def test_strip_code_fences() -> None:
    assert _strip_code_fences("```python\nx = 1\n```") == "x = 1"
    assert _strip_code_fences("```\nx = 1\n```") == "x = 1"
    assert _strip_code_fences("x = 1") == "x = 1"
