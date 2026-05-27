"""TC-6b.5 — the ``note`` tool + ``NoteStore`` (Johnny's journal).

The tool's persistence is real (the ``note`` table), so this is DB-backed and runs
in-network via ``./ctl.sh test``. We prove a note written through the tool persists
with its title/body/tags/ts and reads back newest-first, and that a malformed note
is rejected as a typed ``ValidationError`` at the arg-validation step. (That the
write is Conscience-vetted + lands in ``action_log`` is the dispatch's guarantee,
covered by the dispatch + wiring tests — every tool on the belt inherits it.)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from helpers.db import dispose_global_engine, install_fresh_global_engine, truncate_tables
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.effectors.notes import NoteArgs, NoteStore, NoteTool, notes_to_payload


@pytest_asyncio.fixture
async def note_db(_migrated_test_db: None) -> AsyncIterator[AsyncEngine]:
    """A clean ``note`` table on a fresh, loop-local global engine."""
    engine = install_fresh_global_engine()
    await truncate_tables(("note",))
    try:
        yield engine
    finally:
        await truncate_tables(("note",))
        await dispose_global_engine()


async def test_note_tool_writes_and_reads_back(note_db: AsyncEngine) -> None:
    store = NoteStore()
    tool = NoteTool(store=store)

    result = await tool.run(
        NoteArgs(title="Mars rovers", body="Curiosity is still going.", tags=["space"])
    )

    assert result.success is True
    assert result.output["id"] is not None
    assert result.output["title"] == "Mars rovers"

    notes = await store.recent(10)
    assert len(notes) == 1
    assert notes[0].title == "Mars rovers"
    assert notes[0].body == "Curiosity is still going."
    assert notes[0].tags == ["space"]
    assert notes[0].ts is not None


def _incrementing_clock() -> Callable[[], datetime]:
    """A now_fn returning strictly increasing timestamps (deterministic ordering)."""
    base = datetime(2026, 5, 27, tzinfo=UTC)
    counter = iter(range(1000))
    return lambda: base + timedelta(seconds=next(counter))


async def test_notes_read_newest_first(note_db: AsyncEngine) -> None:
    # Strictly increasing ts so the newest-first ordering is deterministic (three
    # rapid writes could otherwise tie on a real clock).
    store = NoteStore(now_fn=_incrementing_clock())
    await NoteTool(store=store).run(NoteArgs(title="first", body="b1"))
    await NoteTool(store=store).run(NoteArgs(title="second", body="b2"))
    await NoteTool(store=store).run(NoteArgs(title="third", body="b3"))

    titles = [n.title for n in await store.recent(10)]
    assert titles == ["third", "second", "first"]


async def test_notes_to_payload_shape(note_db: AsyncEngine) -> None:
    store = NoteStore()
    await NoteTool(store=store).run(NoteArgs(title="t", body="b", tags=["x", "y"]))

    payload = notes_to_payload(await store.recent(10))
    assert payload[0]["title"] == "t"
    assert payload[0]["tags"] == ["x", "y"]
    assert isinstance(payload[0]["ts"], str)  # ISO string for the wire


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"body": "b"}, id="missing-title"),
        pytest.param({"title": "", "body": "b"}, id="empty-title"),
        pytest.param({"title": "t"}, id="missing-body"),
        pytest.param({"title": "t", "body": ""}, id="empty-body"),
        pytest.param({"title": "t", "body": "b", "extra": "no"}, id="unknown-field"),
    ],
)
def test_bad_note_args_raise_validation_error(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NoteArgs.model_validate(bad)
