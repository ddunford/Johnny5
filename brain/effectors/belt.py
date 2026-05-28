"""Assemble the full tool belt — every 6b tool registered on boot.

6a shipped a registry with only the inert ``noop`` exerciser; 6b fills the belt
with the real tools. This is the one place they're all registered, so the
composition root (and TASK-6b.11's "all tools registered" check) has a single,
testable entry point. Registering a tool here is all it takes to make it
Conscience-vetted + budget-bounded + audited (6a) — no new trust path.

Tools that need a shared runtime collaborator are wired here: ``schedule_wakeup``
gets the SAME ``Scheduler`` the cycle's due-check fires, so a wakeup Johnny
schedules actually wakes him. The rest construct their own (lazy) backings —
SearXNG/HTTP clients and the sandbox launcher only connect per-call, so building
the belt at boot touches no network.
"""

from __future__ import annotations

from brain.effectors.code_exec import CodeExecTool
from brain.effectors.memory_tools import MemorySearchTool, MemoryWriteTool
from brain.effectors.news import NewsTool
from brain.effectors.notes import NoteTool
from brain.effectors.scheduler import Scheduler, ScheduleWakeupTool
from brain.effectors.tools import NoopTool, ToolRegistry
from brain.effectors.web_fetch import WebFetchTool
from brain.effectors.web_search import WebSearchTool


def build_tool_registry(*, scheduler: Scheduler) -> ToolRegistry:
    """A registry with every Phase-6b tool registered (plus the inert ``noop``)."""
    registry = ToolRegistry()
    registry.register(NoopTool())  # the inert pipeline exerciser (kept for diagnostics)
    # network — his discovery + reading surface
    registry.register(WebSearchTool())
    registry.register(NewsTool())
    registry.register(WebFetchTool())
    # exec — sandboxed Python
    registry.register(CodeExecTool())
    # safe — his own journal + memory + self-scheduling
    registry.register(NoteTool())
    registry.register(MemoryWriteTool())
    registry.register(MemorySearchTool())
    registry.register(ScheduleWakeupTool(scheduler=scheduler))  # shares the cycle's Scheduler
    return registry
