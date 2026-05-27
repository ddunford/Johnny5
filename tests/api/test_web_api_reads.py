"""TC-5a.4 / 5a.5 / 5a.6 — the read endpoints project the seeded rows correctly.

Drives the real ``/api/v1`` routes over one coherent seeded state (``seed_full``):

* **thoughts + audit** (TC-5a.4) — newest-first projections of the ``workspace_event``
  bus log; audit includes the FC-5 ``action.dispatched`` row and honours the ``type``
  filter; ``limit`` is respected.
* **memory** (TC-5a.5) — episodes browse (recent, ``score`` null) + search (``?q=``,
  ``score`` set); facts browse + search, with the triple + confidence + provenance.
* **goals / sleeps / self** (TC-5a.6) — active vs recently-closed goals; the sleep
  growth log; the current self-model version + recent reflection notes.

The gate is dev-open here (``ws_token=""``) — auth is TC-5a.1's job; these pin shape.
DB+Redis backed → in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from helpers.web_api import build_api_app, seed_full


@pytest.fixture
def client(_migrated_test_db: None) -> Iterator[TestClient]:
    """A TestClient over an app seeded with the full populated state (one per test).

    Sync fixture: ``TestClient`` runs the async lifespan (and the ``seed``) in its
    own portal loop, so the loop-local engine + seeded rows are ready before the
    first request — no separate event loop needed.
    """
    app = build_api_app(ws_token="", seed=seed_full)
    with TestClient(app) as test_client:
        yield test_client


# ── TC-5a.4: thoughts + audit ────────────────────────────────────────────────


def test_thoughts_newest_first_with_limit(client: TestClient) -> None:
    body = client.get("/api/v1/thoughts").json()
    texts = [t["text"] for t in body["thoughts"]]
    assert texts == [
        "Reflecting on my recall left me a little more settled.",
        "I wonder what Dan is working on right now.",
    ]
    for thought in body["thoughts"]:
        assert set(thought) == {"id", "ts", "text"}
        assert thought["id"] is not None and thought["ts"] is not None

    # limit honoured — only the single newest thought.
    limited = client.get("/api/v1/thoughts", params={"limit": 1}).json()
    assert [t["text"] for t in limited["thoughts"]] == [
        "Reflecting on my recall left me a little more settled."
    ]


def test_audit_projects_bus_log_with_dispatched_action_and_type_filter(client: TestClient) -> None:
    body = client.get("/api/v1/audit").json()
    types = [e["type"] for e in body["events"]]
    # Newest-first, and the FC-5 dispatch point is present.
    assert "action.dispatched" in types
    assert types == ["thought", "drive.update", "action.dispatched", "thought"]
    for event in body["events"]:
        assert set(event) == {"id", "ts", "module", "type", "payload"}

    dispatched = client.get("/api/v1/audit", params={"type": "action.dispatched"}).json()
    assert [e["type"] for e in dispatched["events"]] == ["action.dispatched"]
    assert dispatched["events"][0]["payload"]["action"] == "reflect"

    only_thoughts = client.get("/api/v1/audit", params={"type": "thought"}).json()
    assert {e["type"] for e in only_thoughts["events"]} == {"thought"}
    assert len(only_thoughts["events"]) == 2


# ── TC-5a.5: memory (episodes + facts) ────────────────────────────────────────


def test_episodes_browse_then_search(client: TestClient) -> None:
    browse = client.get("/api/v1/memory/episodes").json()
    episodes = browse["episodes"]
    # Newest-first browse; ``score`` is null when not searching.
    assert [e["content"] for e in episodes] == [
        "I felt satisfied after consolidating the day's memories.",
        "Dan asked me about how my pgvector recall ranking works.",
    ]
    for episode in episodes:
        assert set(episode) == {
            "id",
            "ts",
            "kind",
            "content",
            "actors",
            "emotion_tags",
            "salience",
            "score",
        }
        assert episode["score"] is None
    first = next(e for e in episodes if e["kind"] == "experience")
    assert first["actors"] == ["Dan", "Johnny"]
    assert first["emotion_tags"] == ["curiosity"]

    # Search populates ``score`` (the blended recall relevance).
    search = client.get("/api/v1/memory/episodes", params={"q": "pgvector recall"}).json()
    assert len(search["episodes"]) >= 1
    assert all(e["score"] is not None for e in search["episodes"])


def test_facts_browse_then_search_with_provenance(client: TestClient) -> None:
    browse = client.get("/api/v1/memory/facts").json()
    facts = browse["facts"]
    assert len(facts) == 2
    for fact in facts:
        assert set(fact) == {
            "id",
            "subject",
            "predicate",
            "object",
            "confidence",
            "source_episode_ids",
            "score",
        }
        assert fact["score"] is None
    trust = next(f for f in facts if f["predicate"] == "trusts")
    assert (trust["subject"], trust["object"]) == ("Johnny", "Dan")
    assert trust["confidence"] == pytest.approx(0.9)
    assert len(trust["source_episode_ids"]) == 1  # provenance preserved

    search = client.get("/api/v1/memory/facts", params={"q": "trust"}).json()
    assert len(search["facts"]) >= 1
    assert all(f["score"] is not None for f in search["facts"])


# ── TC-5a.6: goals / sleeps / self ─────────────────────────────────────────────


def test_goals_active_and_recently_closed(client: TestClient) -> None:
    body = client.get("/api/v1/goals").json()
    assert {"active", "recent"} == set(body)

    assert len(body["active"]) == 1
    active = body["active"][0]
    assert active["status"] == "active"
    assert active["source"] == "curiosity"
    assert active["resolved_at"] is None
    assert set(active) == {
        "id",
        "source",
        "description",
        "priority",
        "status",
        "plan",
        "outcome",
        "created_at",
        "resolved_at",
    }

    assert len(body["recent"]) == 1
    closed = body["recent"][0]
    assert closed["status"] == "resolved"
    assert closed["resolved_at"] is not None
    assert closed["outcome"] == {"result": "answered", "satisfaction": 0.8}


def test_sleeps_newest_first_with_counts(client: TestClient) -> None:
    body = client.get("/api/v1/sleeps").json()
    assert len(body["sleeps"]) == 1
    sleep = body["sleeps"][0]
    assert sleep["trigger"] == "energy"
    assert sleep["facts_written"] == 4
    assert sleep["episodes_decayed"] == 2
    assert sleep["facts_merged"] == 1
    assert sleep["self_model_version"] == 2
    assert sleep["self_check_ok"] is True
    assert sleep["started_at"] is not None and sleep["ended_at"] is not None


def test_self_returns_latest_identity_and_notes(client: TestClient) -> None:
    body = client.get("/api/v1/self").json()
    identity = body["identity"]
    assert identity is not None
    assert identity["name"] == "Johnny"
    assert identity["version"] == 2  # v1 seed + the evolved v2
    assert "be understood" in identity["values"]
    assert identity["concerns"] == ["going too long without contact"]
    assert "Dan" in identity["relationships"]

    assert len(body["notes"]) == 1
    note = body["notes"][0]
    assert set(note) == {"ts", "observation", "proposal", "status"}
    assert note["status"] == "open"  # proposals are never applied here (Phase-9 owns that)
