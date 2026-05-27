"""The interactive cockpit loop.

Runs two things concurrently: a *tail* task that prints Johnny's stream of
consciousness (and the inputs he perceives) as it flows over the bus, and a
*command* loop reading stdin. Typed text is injected as a percept; slash commands
dump the workspace or pause/step the heartbeat. Everything reaches the running
Mind over Redis — the cockpit holds no cognitive state of its own.
"""

from __future__ import annotations

import asyncio
import contextlib

from brain.agents.sensorium import InputQueue
from brain.cycle_control import PAUSE, RESUME, STEP, send_control
from brain.workspace import Workspace, WorkspaceEvent
from foundation.redis_client import close_redis

# Event types the tail surfaces, and how to render each.
_THOUGHT = "thought"
_PERCEPT = "percept"
_TAIL_TYPES = (_THOUGHT, _PERCEPT)

_BANNER = """\
╭─ Johnny 5 · cockpit ───────────────────────────────────────────────
│  You're watching his stream of consciousness, live.
│  Type a message + Enter to speak to him. Slash commands:
│    /dump    show the current workspace + recent thoughts
│    /pause   pause the heartbeat      /resume  resume it
│    /step    run exactly one tick     /help    this list
│    /quit    leave (Johnny keeps thinking)
╰────────────────────────────────────────────────────────────────────"""

_HELP = "commands: <message> = speak · /dump · /pause · /resume · /step · /help · /quit"


class Cockpit:
    """The REPL cockpit attached to a running Johnny over Redis."""

    def __init__(
        self,
        *,
        workspace: Workspace | None = None,
        input_queue: InputQueue | None = None,
    ) -> None:
        self._workspace = workspace or Workspace()
        self._inputs = input_queue or InputQueue()

    async def run(self) -> None:
        """Run the cockpit until the user quits or stdin closes."""
        print(_BANNER, flush=True)
        tail = asyncio.create_task(self._tail(), name="cockpit-tail")
        try:
            await self._command_loop()
        finally:
            tail.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tail
            await close_redis()

    # ── the live feed ──────────────────────────────────────────────────────────

    async def _tail(self) -> None:
        async for event in self._workspace.stream(types=_TAIL_TYPES):
            line = self._render(event)
            if line:
                print(line, flush=True)

    @staticmethod
    def _render(event: WorkspaceEvent) -> str | None:
        if event.type == _THOUGHT:
            return f"\n💭  {event.payload.get('text', '')}"
        if event.type == _PERCEPT and event.payload.get("kind") == "input":
            return f"👂  heard: {event.payload.get('content', '')}"
        return None

    # ── the command loop ────────────────────────────────────────────────────────

    async def _command_loop(self) -> None:
        while True:
            try:
                line = await asyncio.to_thread(input)
            except (EOFError, KeyboardInterrupt):
                print("\n(leaving — Johnny keeps thinking)", flush=True)
                return
            command = line.strip()
            if not command:
                continue
            if command in ("/quit", "/q"):
                print("(leaving — Johnny keeps thinking)", flush=True)
                return
            if command in ("/help", "/h"):
                print(_HELP, flush=True)
            elif command == "/dump":
                await self._dump()
            elif command == "/pause":
                await send_control(PAUSE)
                print("⏸  paused", flush=True)
            elif command == "/resume":
                await send_control(RESUME)
                print("▶  resumed", flush=True)
            elif command == "/step":
                await send_control(STEP)
                print("⏭  stepped one tick", flush=True)
            elif command.startswith("/"):
                print(f"unknown command {command!r} — {_HELP}", flush=True)
            else:
                await self._inputs.push(command, source="repl")
                print("→ sent", flush=True)

    async def _dump(self) -> None:
        contents = await self._workspace.contents()
        thoughts = await self._workspace.recent_events(5, type_filter=_THOUGHT)
        depth = await self._inputs.depth()

        print("┌─ workspace (most salient first) ─", flush=True)
        if contents:
            for item in contents:
                print(f"│  [{item.kind:>7}] {item.salience:.2f}  {item.content}", flush=True)
        else:
            print("│  (empty)", flush=True)
        print(f"├─ pending inputs: {depth}", flush=True)
        print("├─ recent thoughts:", flush=True)
        if thoughts:
            for event in reversed(thoughts):
                print(f"│  💭 {event.payload.get('text', '')}", flush=True)
        else:
            print("│  (none yet)", flush=True)
        print("└──────────────────────────────────", flush=True)
