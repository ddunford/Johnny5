"""The Global Workspace — the blackboard + event bus the whole society shares.

This is the coordination substrate of the Mind (``SPEC §4, §7``). Inner agents do
**not** call each other; they broadcast typed events onto the bus and read the
current salient contents off the blackboard. Two distinct surfaces live here:

* **The bus** — ``broadcast(event)`` publishes a typed ``WorkspaceEvent`` over
  Redis pub/sub *and* persists it to ``workspace_event`` (every broadcast is
  logged, for observability/replay). ``subscribe(type, handler)`` registers an
  in-process handler; ``run()`` is the listener loop that dispatches received
  events to handlers (and re-broadcasts whatever they emit, depth-capped so a
  reflex loop can't storm the bus). ``stream()`` is a read-only consumer feed for
  the REPL and the ``/ws/consciousness`` socket.
* **The blackboard** — ``set_contents`` / ``contents`` hold the *current* bounded
  salient set. Attention (the bottleneck) replaces it each tick; the Narrator and
  the REPL read it. Replace-semantics keep it bounded by construction.

The Redis client and clock are injectable so the bus is testable without real
time. Nothing here imports ``core`` (FC-1) and every agent reaches the rest of the
society only through this object (FC-2/FC-8).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import cast

from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis
from sqlalchemy import BigInteger, DateTime, Index, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from foundation.db import Base, Repository, session_scope
from foundation.observability import get_logger
from foundation.redis_client import get_redis

_log = get_logger("brain.workspace")

WORKSPACE_EVENT_TABLE = "workspace_event"

# Redis pub/sub channel every broadcast is published on, and the key holding the
# current blackboard contents. Internal wiring, not user-tunable — overridable in
# tests via the constructor (namespaced so parallel tests don't collide).
DEFAULT_CHANNEL = "johnny:workspace:bus"
DEFAULT_CONTENTS_KEY = "johnny:workspace:contents"

# A handler reacting to a re-broadcast of its own output type could loop forever;
# cap how many hops an event chain may take before the bus stops re-dispatching.
DEFAULT_MAX_HOPS = 8


def _utcnow() -> datetime:
    """Default wall clock for event timestamps (injected so tests can freeze it)."""
    return datetime.now(UTC)


# ── domain types ─────────────────────────────────────────────────────────────


class WorkspaceEvent(BaseModel):
    """A typed broadcast on the bus.

    ``module`` is the publishing agent/subsystem; ``type`` is the event kind
    (subscribers key off it); ``payload`` is the structured body. ``id``/``ts``
    are populated by ``broadcast`` (id from the persisted row). ``hops`` counts
    re-broadcasts in a handler chain so the bus can break runaway reflex loops.
    """

    id: int | None = None
    ts: datetime | None = None
    module: str
    type: str
    payload: dict[str, object] = Field(default_factory=dict)
    hops: int = 0


class WorkspaceItem(BaseModel):
    """One salient item on the blackboard (what Attention selected this tick).

    ``content`` is the text the Narrator reads; ``kind`` distinguishes a percept
    from a recalled memory or a working-memory item; ``salience`` is the score
    Attention assigned. Free-form ``metadata`` carries provenance (episode id,
    percept id) without widening the schema.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: str
    content: str
    salience: float = 0.5
    source: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


# Handlers may emit follow-up events (which the bus re-broadcasts) or nothing.
EventHandler = Callable[[WorkspaceEvent], Awaitable[Sequence[WorkspaceEvent] | None]]


# ── persistence ──────────────────────────────────────────────────────────────


class WorkspaceEventRow(Base):
    """The ``workspace_event`` table — the append-only bus log."""

    __tablename__ = WORKSPACE_EVENT_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default="{}")

    __table_args__ = (
        Index("ix_workspace_event_ts", "ts"),
        Index("ix_workspace_event_type", "type"),
    )


class WorkspaceEventRepository(Repository[WorkspaceEventRow]):
    """Session-scoped persistence + recent-history reads for the bus log."""

    model = WorkspaceEventRow

    async def recent(
        self, limit: int, *, type_filter: str | None = None
    ) -> list[WorkspaceEventRow]:
        """Most recent events first (feeds the REPL dump and replay)."""
        stmt = select(WorkspaceEventRow).order_by(WorkspaceEventRow.ts.desc()).limit(limit)
        if type_filter is not None:
            stmt = stmt.where(WorkspaceEventRow.type == type_filter)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ── the workspace ────────────────────────────────────────────────────────────


class Workspace:
    """The Global Workspace: pub/sub bus + persisted log + the salient blackboard."""

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        channel: str = DEFAULT_CHANNEL,
        contents_key: str = DEFAULT_CONTENTS_KEY,
        max_hops: int = DEFAULT_MAX_HOPS,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._redis = redis or get_redis()
        self._channel = channel
        self._contents_key = contents_key
        self._max_hops = max_hops
        self._now_fn = now_fn
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._listening = False

    # ── bus: publish ─────────────────────────────────────────────────────────

    async def broadcast(self, event: WorkspaceEvent) -> WorkspaceEvent:
        """Persist an event to ``workspace_event`` then publish it on the bus.

        Returns the event with ``id``/``ts`` populated. Persist-then-publish so
        the durable log can't miss a broadcast that subscribers saw.
        """
        stamped = event if event.ts is not None else event.model_copy(update={"ts": self._now_fn()})
        persisted = await self._persist(stamped)
        await self._publish(persisted)
        return persisted

    async def _persist(self, event: WorkspaceEvent) -> WorkspaceEvent:
        async with session_scope() as session:
            row = await WorkspaceEventRepository(session).add(
                WorkspaceEventRow(
                    ts=event.ts,
                    module=event.module,
                    type=event.type,
                    payload=dict(event.payload),
                )
            )
            return event.model_copy(update={"id": row.id, "ts": row.ts})

    async def _publish(self, event: WorkspaceEvent) -> None:
        await cast(
            "Awaitable[int]",
            self._redis.publish(self._channel, event.model_dump_json()),
        )

    # ── bus: subscribe + dispatch ──────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Register an in-process handler for an event ``type`` (``"*"`` = all)."""
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Drop a previously-registered handler (so an agent can be retired, FC-2)."""
        handlers = self._subscribers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def _handlers_for(self, event_type: str) -> list[EventHandler]:
        return [*self._subscribers.get(event_type, []), *self._subscribers.get("*", [])]

    async def dispatch(self, event: WorkspaceEvent) -> None:
        """Invoke every handler subscribed to ``event.type``; re-broadcast emissions.

        A handler that fails is logged and skipped — one broken reflex must not
        stop the bus (graceful degradation). Emissions are re-broadcast with an
        incremented hop count and dropped once ``max_hops`` is reached.
        """
        for handler in self._handlers_for(event.type):
            try:
                emitted = await handler(event)
            except Exception:
                _log.exception("workspace.handler_error", event_type=event.type)
                continue
            if not emitted:
                continue
            if event.hops >= self._max_hops:
                _log.warning("workspace.max_hops_reached", event_type=event.type, hops=event.hops)
                continue
            for out in emitted:
                await self.broadcast(out.model_copy(update={"hops": event.hops + 1}))

    async def run(self) -> None:
        """Listen on the bus and dispatch received events until ``stop`` is called.

        Uses a short polled read so ``stop()`` takes effect promptly without a
        busy-spin. A malformed payload is logged and skipped, never fatal.
        """
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        self._listening = True
        try:
            while self._listening:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                event = self._parse(message["data"])
                if event is not None:
                    await self.dispatch(event)
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()

    def stop(self) -> None:
        """Ask the ``run`` listener loop to exit on its next iteration."""
        self._listening = False

    # ── bus: read-only consumer feed ───────────────────────────────────────────

    async def stream(self, *, types: Sequence[str] | None = None) -> AsyncIterator[WorkspaceEvent]:
        """Yield live events off the bus (the REPL tail and ``/ws/consciousness``).

        Read-only: a consumer never dispatches to agents. ``types`` filters by
        event type; ``None`` yields everything.
        """
        wanted = set(types) if types is not None else None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                event = self._parse(message["data"])
                if event is None:
                    continue
                if wanted is None or event.type in wanted:
                    yield event
        finally:
            await pubsub.unsubscribe(self._channel)
            await pubsub.aclose()

    @staticmethod
    def _parse(data: object) -> WorkspaceEvent | None:
        try:
            return WorkspaceEvent.model_validate_json(cast("str | bytes", data))
        except ValidationError:
            _log.warning("workspace.malformed_event")
            return None

    # ── blackboard: the current salient contents ────────────────────────────────

    async def set_contents(self, items: Sequence[WorkspaceItem]) -> None:
        """Replace the blackboard with ``items`` (Attention's bounded selection).

        Replace, never append — the workspace stays bounded by construction; the
        bottleneck (which items make it in) is Attention's job, the bound is ours.
        """
        payload = "[" + ",".join(item.model_dump_json() for item in items) + "]"
        await cast("Awaitable[int]", self._redis.set(self._contents_key, payload))

    async def contents(self) -> list[WorkspaceItem]:
        """The current salient set, most-salient first (what the Narrator reads)."""
        raw = await cast("Awaitable[str | None]", self._redis.get(self._contents_key))
        if not raw:
            return []
        items = [WorkspaceItem.model_validate(obj) for obj in _loads_list(raw)]
        items.sort(key=lambda i: i.salience, reverse=True)
        return items

    async def clear_contents(self) -> None:
        """Empty the blackboard."""
        await cast("Awaitable[int]", self._redis.delete(self._contents_key))

    # ── log reads (REPL / replay) ───────────────────────────────────────────────

    async def recent_events(
        self, limit: int = 50, *, type_filter: str | None = None
    ) -> list[WorkspaceEvent]:
        """Recent persisted events, newest first."""
        async with session_scope() as session:
            rows = await WorkspaceEventRepository(session).recent(limit, type_filter=type_filter)
        return [
            WorkspaceEvent(
                id=row.id,
                ts=row.ts,
                module=row.module,
                type=row.type,
                payload=row.payload,
            )
            for row in rows
        ]


def _loads_list(raw: str) -> list[dict[str, object]]:
    import json

    parsed = json.loads(raw)
    return parsed if isinstance(parsed, list) else []
