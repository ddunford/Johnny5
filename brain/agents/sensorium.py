"""Sensorium — the perception agent (``SPEC §5`` #1).

Turns raw inputs into normalised ``Percept`` rows and the ``WorkspaceItem``s the
rest of the tick reasons over. Two sources this phase:

* **Inbound messages** — the REPL (and later voice/UI) push raw text onto a Redis
  ``InputQueue``; ``perceive`` drains it each tick (``SPEC §7``: "Sensorium pulls
  new inputs"). A message is a *high-salience interrupt* — it wins attention and
  shifts the next thought, but still flows through the full cycle, which is why
  talking to Johnny feels continuous rather than stateless.
* **Ambient system metrics** — a low-salience sense of the host's state and time
  passing, so the workspace is never empty and an idle Johnny has something to
  narrate (the seed of the "it's quiet, I want input" beat).

The input queue is sensory *ingress* from an interface, not inter-agent chatter —
inner agents still talk only over the workspace bus. Sensorium is driven by the
cycle's PERCEIVE stage; it subscribes to no bus events, so ``handle`` is a no-op.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from brain.workspace import WorkspaceEvent, WorkspaceItem
from foundation.config import get_settings
from foundation.db import Base, Repository, session_scope
from foundation.redis_client import get_redis

PERCEPT_TABLE = "percept"
SENSORIUM_AGENT_NAME = "sensorium"

# Redis list the interfaces push raw inputs onto; Sensorium drains it each tick.
DEFAULT_INPUT_QUEUE_KEY = "johnny:sensorium:inputs"
# Cap how many queued inputs one tick ingests, so a flood is spread across ticks
# rather than swamping a single perceive (attention is the salience bottleneck;
# this is just back-pressure on ingestion).
_MAX_DRAIN_PER_TICK = 16

MODALITY_TEXT = "text"
MODALITY_SYSTEM = "system"
SOURCE_SYSTEM_METRICS = "system_metrics"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── persistence ──────────────────────────────────────────────────────────────


class PerceptRow(Base):
    """The ``percept`` table — one row per normalised input."""

    __tablename__ = PERCEPT_TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    modality: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    normalised: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    source: Mapped[str] = mapped_column(String(128), nullable=False)


class Percept(BaseModel):
    """A normalised input, decoupled from the ORM/session."""

    id: int | None = None
    ts: datetime | None = None
    modality: str
    raw: str
    normalised: dict[str, Any] = Field(default_factory=dict)
    source: str


class PerceptRepository(Repository[PerceptRow]):
    """Session-scoped persistence for ``percept`` rows."""

    model = PerceptRow


# ── raw input ingress ──────────────────────────────────────────────────────────


class RawInput(BaseModel):
    """A raw input as an interface queued it, before normalisation."""

    raw: str
    source: str
    modality: str = MODALITY_TEXT


class InputQueue:
    """A Redis-list queue of pending raw inputs (interface → Sensorium).

    Interfaces ``push`` onto the tail; ``drain`` pops a bounded batch from the
    head each tick. FIFO so messages are perceived in the order they arrived.
    """

    def __init__(self, *, redis: Redis | None = None, key: str = DEFAULT_INPUT_QUEUE_KEY) -> None:
        self._redis = redis or get_redis()
        self._key = key

    async def push(self, raw: str, *, source: str, modality: str = MODALITY_TEXT) -> None:
        """Enqueue a raw input for the next tick to perceive."""
        item = RawInput(raw=raw, source=source, modality=modality)
        await cast("Any", self._redis.rpush(self._key, item.model_dump_json()))

    async def drain(self, max_items: int = _MAX_DRAIN_PER_TICK) -> list[RawInput]:
        """Pop up to ``max_items`` queued inputs (FIFO); empty list when idle."""
        raw_items = await cast("Any", self._redis.lpop(self._key, max_items))
        if not raw_items:
            return []
        if isinstance(raw_items, str):  # single-item lpop without count
            raw_items = [raw_items]
        return [RawInput.model_validate_json(r) for r in raw_items]

    async def depth(self) -> int:
        """How many inputs are waiting (for the REPL dump)."""
        return int(await cast("Any", self._redis.llen(self._key)))


# ── the agent ────────────────────────────────────────────────────────────────


class Sensorium:
    """Normalises queued inputs + an ambient system percept into the tick."""

    name = SENSORIUM_AGENT_NAME
    subscribes_to: Sequence[str] = ()
    prompt = ""  # pure normalisation this phase — no LLM call, so no prompt
    model_route = "perception"  # reserved for vision captioning (later phases)

    def __init__(
        self,
        *,
        input_queue: InputQueue | None = None,
        now_fn: Callable[[], datetime] = _utcnow,
    ) -> None:
        settings = get_settings()
        self._queue = input_queue or InputQueue()
        self._now_fn = now_fn
        self._input_salience = settings.sensorium_input_salience
        self._ambient_salience = settings.sensorium_ambient_salience
        self._ambient_persist_every = max(1, settings.sensorium_ambient_persist_every_ticks)
        self._ticks = 0

    async def perceive(self) -> Sequence[WorkspaceItem]:
        """Drain queued inputs + sample the ambient system percept for this tick."""
        self._ticks += 1
        items: list[WorkspaceItem] = []

        for raw in await self._queue.drain():
            items.append(await self._perceive_input(raw))

        items.append(await self._perceive_system())
        return items

    async def handle(self, event: WorkspaceEvent) -> Sequence[WorkspaceEvent]:
        """Pipeline-driven agent — reacts to no bus events."""
        return ()

    # ── per-source normalisation ───────────────────────────────────────────────

    async def _perceive_input(self, raw: RawInput) -> WorkspaceItem:
        """Normalise an inbound message into a persisted percept + workspace item."""
        text = raw.raw.strip()
        normalised: dict[str, Any] = {"text": text, "source": raw.source}
        percept = await self._persist(
            Percept(
                modality=raw.modality,
                raw=raw.raw,
                normalised=normalised,
                source=raw.source,
                ts=self._now_fn(),
            )
        )
        return WorkspaceItem(
            kind="input",
            content=text,
            salience=self._input_salience,
            source=raw.source,
            metadata={"percept_id": percept.id, "modality": raw.modality},
        )

    async def _perceive_system(self) -> WorkspaceItem:
        """Sample the ambient host state; persist it only on the slow sub-cadence."""
        now = self._now_fn()
        normalised = _system_metrics(now)
        content = _describe_system(normalised)

        percept_id: int | None = None
        if self._ticks % self._ambient_persist_every == 1 or self._ambient_persist_every == 1:
            percept = await self._persist(
                Percept(
                    modality=MODALITY_SYSTEM,
                    raw=json.dumps(normalised, sort_keys=True),
                    normalised=normalised,
                    source=SOURCE_SYSTEM_METRICS,
                    ts=now,
                )
            )
            percept_id = percept.id

        return WorkspaceItem(
            kind="ambient",
            content=content,
            salience=self._ambient_salience,
            source=SOURCE_SYSTEM_METRICS,
            metadata={"percept_id": percept_id, **normalised},
        )

    async def _persist(self, percept: Percept) -> Percept:
        async with session_scope() as session:
            row = await PerceptRepository(session).add(
                PerceptRow(
                    ts=percept.ts,
                    modality=percept.modality,
                    raw=percept.raw,
                    normalised=dict(percept.normalised),
                    source=percept.source,
                )
            )
            return percept.model_copy(update={"id": row.id, "ts": row.ts})


# ── system metrics (dependency-free, host-portable) ────────────────────────────


def _system_metrics(now: datetime) -> dict[str, Any]:
    """A small, dependency-free snapshot of the host + time."""
    metrics: dict[str, Any] = {"time": now.isoformat()}
    try:
        load1, load5, load15 = os.getloadavg()
        metrics["load_1m"] = round(load1, 2)
        metrics["load_5m"] = round(load5, 2)
        metrics["load_15m"] = round(load15, 2)
    except (OSError, AttributeError):
        # getloadavg is unavailable on some platforms — degrade to time-only.
        pass
    return metrics


def _describe_system(metrics: dict[str, Any]) -> str:
    """A human-readable ambient line the Narrator can weave into a thought."""
    when = metrics.get("time", "")
    load = metrics.get("load_1m")
    clock = when[11:16] if isinstance(when, str) and len(when) >= 16 else when
    if load is not None:
        return f"All quiet — it's {clock} UTC, the host is idling (load {load})."
    return f"All quiet — it's {clock} UTC, nothing new has come in."
