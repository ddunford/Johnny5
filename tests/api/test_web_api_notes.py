"""TC-6b.11 — ``GET /api/v1/notes`` projects the journal newest-first.

The read surface for Johnny's journal (the ``note`` table, written by the ``note``
tool). Pins the projection shape + ordering + the empty default (a fresh Mind has
no notes — the endpoint must return ``{"notes": []}``, not error). DB-backed,
in-network. The captured wire fixture + frontend contract are qa's (#14).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from helpers.web_api import build_api_app, seed_journal_note

_T0 = datetime(2026, 5, 20, 9, 0, 0, tzinfo=UTC)


async def _seed_notes(_runtime: SimpleNamespace) -> None:
    await seed_journal_note(title="first", body="b1", tags=["a"], ts=_T0)
    await seed_journal_note(title="second", body="b2", tags=["b", "c"], ts=_T0.replace(hour=10))


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = build_api_app(ws_token="", seed=_seed_notes)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def empty_client() -> Iterator[TestClient]:
    app = build_api_app(ws_token="")  # no seed — a fresh, note-less Mind
    with TestClient(app) as test_client:
        yield test_client


def test_notes_newest_first_with_shape(client: TestClient) -> None:
    body = client.get("/api/v1/notes").json()
    titles = [n["title"] for n in body["notes"]]
    assert titles == ["second", "first"]  # newest first
    for note in body["notes"]:
        assert set(note) == {"id", "ts", "title", "body", "tags"}
        assert note["id"] is not None and note["ts"] is not None
    assert body["notes"][0]["tags"] == ["b", "c"]


def test_notes_limit_honoured(client: TestClient) -> None:
    body = client.get("/api/v1/notes", params={"limit": 1}).json()
    assert [n["title"] for n in body["notes"]] == ["second"]


def test_notes_empty_default(empty_client: TestClient) -> None:
    # The first-ever load has zero rows — the endpoint returns the empty default,
    # not an error (the panel's empty-state contract, P5b crash-on-first-load lesson).
    body = empty_client.get("/api/v1/notes").json()
    assert body == {"notes": []}
