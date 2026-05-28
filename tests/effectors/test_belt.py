"""TC-6b.11 — the boot tool belt: every 6b tool registered, with its hazard class.

``build_tool_registry`` is the single place the belt is assembled (the composition
root + this test read it). Pure, host-runnable: registration touches no network
(clients/launcher connect per-call) and runs no tool.
"""

from __future__ import annotations

from brain.effectors.belt import build_tool_registry
from brain.effectors.scheduler import Scheduler
from brain.effectors.tools import DangerClass

_EXPECTED = {
    "noop",
    "web_search",
    "news",
    "web_fetch",
    "code_exec",
    "note",
    "memory_write",
    "memory_search",
    "schedule_wakeup",
}


def test_belt_registers_every_tool() -> None:
    registry = build_tool_registry(scheduler=Scheduler())
    assert set(registry.names()) == _EXPECTED


def test_belt_tools_declare_the_right_hazard_classes() -> None:
    registry = build_tool_registry(scheduler=Scheduler())
    # network — the world-reaching tools
    assert registry.resolve("web_search").danger is DangerClass.NETWORK
    assert registry.resolve("news").danger is DangerClass.NETWORK
    assert registry.resolve("web_fetch").danger is DangerClass.NETWORK
    # exec — sandboxed code
    assert registry.resolve("code_exec").danger is DangerClass.EXEC
    # safe — his own journal/memory/scheduling
    for name in ("note", "memory_write", "memory_search", "schedule_wakeup"):
        assert registry.resolve(name).danger is DangerClass.SAFE


def test_schedule_wakeup_shares_the_injected_scheduler() -> None:
    # The schedule_wakeup tool must drive the SAME Scheduler the cycle's due-check
    # fires, or a scheduled wakeup would never wake him.
    scheduler = Scheduler()
    registry = build_tool_registry(scheduler=scheduler)
    tool = registry.resolve("schedule_wakeup")
    assert tool._scheduler is scheduler  # type: ignore[attr-defined]
