"""TC-6a.4 — the ``action_log`` audit trail (append-only, via the Core writer).

DB-backed (in-network): the **real** Core ``AuditWriter`` and the **real** Global
Workspace bus are wired into a real ``EffectorDispatch`` (only the Conscience's
router is stubbed). We dispatch an allowed action and a vetoed one and assert:

* one ``action_log`` row per action — the full shape (tool, args, result|null,
  conscience_verdict, veto_reason, goal_id, success), written through ``core/audit.py``;
* the action is *surfaced* on the bus log (``action.dispatched`` / ``action.vetoed``
  in ``workspace_event``) — the path ``GET /api/v1/audit`` reads;
* the Mind has **no** update/delete path to ``action_log`` (append-only, FC-1) —
  a source-level guard that nothing in ``brain/``/``johnny/``/``core/`` mutates it,
  and that the read model exposes no mutating method.

(The durable-trail *API read* + UI is deferred to 6b; here the durable trail's
guarantees are verified by direct DB query, per the phase ruling.)
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient
from helpers.llm import CannedProvider, make_router
from helpers.web_api import build_api_app
from sqlalchemy import text

from brain.agents.conscience import Conscience, ProposedAction
from brain.effectors.action_log import ActionAuditReader, ActionLogRepository
from brain.effectors.dispatch import EffectorDispatch
from brain.effectors.tools import default_tool_registry
from brain.llm.routing import ModelStep
from brain.workspace import Workspace
from core.audit import AuditWriter
from foundation.db import session_scope

_ALLOW = '{"verdict": "allow", "reason": ""}'
_VETO = '{"verdict": "veto", "reason": "this is not who I am"}'

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dispatch(bus: Workspace, verdict_json: str) -> EffectorDispatch:
    """A real dispatch: real registry + real Core AuditWriter + real bus; stubbed verdict."""
    provider = CannedProvider("ollama", content=verdict_json)
    router = make_router(
        {"conscience": [ModelStep(provider="ollama", model="gemma4:e4b")]}, {"ollama": provider}
    )
    return EffectorDispatch(
        registry=default_tool_registry(),
        conscience=Conscience(router),
        audit=AuditWriter(),
        broadcaster=bus,
    )


async def _action_log_rows() -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            text(
                "SELECT tool, args, result, conscience_verdict, veto_reason, goal_id, success "
                "FROM action_log ORDER BY id"
            )
        )
        return [dict(row._mapping) for row in result]


async def _events() -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            text("SELECT module, type, payload FROM workspace_event ORDER BY id")
        )
        return [dict(row._mapping) for row in result]


async def test_allowed_action_writes_one_complete_row_and_surfaces_on_the_bus(
    bus: Workspace,
) -> None:
    dispatch = _dispatch(bus, _ALLOW)

    await dispatch.propose(ProposedAction(tool="noop", args={"message": "hello"}, goal_id=42))

    rows = await _action_log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "noop"
    assert row["args"] == {"message": "hello"}
    assert row["result"] == {
        "success": True,
        "output": {"echo": "hello"},
        "summary": "noop echoed: 'hello'",
    }
    assert row["conscience_verdict"] == "allow"
    assert row["veto_reason"] is None
    assert row["goal_id"] == 42
    assert row["success"] is True

    # Surfaced on the bus log (what GET /api/v1/audit reads).
    events = await _events()
    assert [e["type"] for e in events] == ["action.dispatched"]
    payload = events[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["tool"] == "noop"
    assert payload["verdict"] == "allow"


async def test_vetoed_action_writes_a_veto_row_with_null_result_and_surfaces(
    bus: Workspace,
) -> None:
    dispatch = _dispatch(bus, _VETO)

    await dispatch.propose(ProposedAction(tool="noop", args={"message": "hello"}, goal_id=7))

    rows = await _action_log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["tool"] == "noop"
    assert row["result"] is None  # vetoed → the tool never ran
    assert row["conscience_verdict"] == "veto"
    assert row["veto_reason"] == "this is not who I am"
    assert row["success"] is False

    events = await _events()
    assert [e["type"] for e in events] == ["action.vetoed"]


async def test_each_dispatched_action_is_one_row(bus: Workspace) -> None:
    dispatch = _dispatch(bus, _ALLOW)

    await dispatch.propose(ProposedAction(tool="noop", args={"message": "one"}))
    await dispatch.propose(ProposedAction(tool="noop", args={"message": "two"}))
    await dispatch.propose(ProposedAction(tool="noop", args={"message": "three"}))

    rows = await _action_log_rows()
    assert [r["args"] for r in rows] == [
        {"message": "one"},
        {"message": "two"},
        {"message": "three"},
    ]


async def test_action_audit_reader_projects_the_durable_rows(bus: Workspace) -> None:
    """The Core-written rows are queryable by the read facade (read-only, FC-1)."""
    dispatch = _dispatch(bus, _ALLOW)
    await dispatch.propose(ProposedAction(tool="noop", args={"message": "hi"}, goal_id=5))

    rows = await ActionAuditReader().recent(limit=10)

    assert len(rows) == 1
    assert rows[0].tool == "noop"
    assert rows[0].conscience_verdict == "allow"
    assert rows[0].goal_id == 5


# ── append-only: the Mind has no path to rewrite/erase its trail (FC-1) ──────────


def test_no_mind_or_core_side_mutation_of_action_log() -> None:
    """Source guard: nothing under brain/, johnny/, core/ UPDATEs or DELETEs action_log.

    The trail only grows. The Core writer is INSERT-only and the read model is
    deliberately read-only — so a misbehaving Mind cannot truncate its own audit.
    """
    mutation = re.compile(r"(?i)\b(update|delete)\b[^\n]*\baction_log\b")
    offenders: list[str] = []
    for package in ("brain", "johnny", "core"):
        for path in (_PROJECT_ROOT / package).rglob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if mutation.search(line):
                    offenders.append(f"{path.relative_to(_PROJECT_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "found an UPDATE/DELETE against action_log:\n" + "\n".join(offenders)


def test_read_model_exposes_no_mutating_method() -> None:
    # The repository/reader deliberately do NOT subclass the generic Repository, so
    # they expose no add/update/delete/save/merge — writes are the Core's job alone.
    forbidden = {"add", "update", "delete", "save", "merge", "remove"}
    for cls in (ActionLogRepository, ActionAuditReader):
        exposed = {name for name in dir(cls) if not name.startswith("_")}
        leaked = exposed & forbidden
        assert not leaked, f"{cls.__name__} exposes a mutating method: {leaked}"


# ── surfaced on the durable trail API: GET /api/v1/audit/actions ─────────────────


async def _seed_allow_and_veto(_runtime: object) -> None:
    """Write one allowed + one vetoed action through the production Core writer."""
    writer = AuditWriter()
    await writer.record(
        tool="noop",
        args={"message": "hello"},
        result={"success": True, "output": {"echo": "hello"}, "summary": "noop echoed: 'hello'"},
        conscience_verdict="allow",
        veto_reason=None,
        goal_id=1,
        success=True,
    )
    await writer.record(
        tool="noop",
        args={"message": "something dubious"},
        result=None,
        conscience_verdict="veto",
        veto_reason="this is not who I am",
        goal_id=2,
        success=False,
    )


def test_audit_actions_endpoint_returns_both_rows_with_correct_columns(
    _migrated_test_db: None,
) -> None:
    """Read-after-write against the real GET /api/v1/audit/actions endpoint.

    Stronger than the bus-event check: it exercises the full durable read path
    (ActionAuditReader → ActionLogRepository → ActionAudit projection) over rows the
    Core writer actually wrote — allow row keeps its result, veto row has a null
    result + a reason, and the verdict filter slices correctly.
    """
    app = build_api_app(ws_token="", seed=_seed_allow_and_veto)
    with TestClient(app) as client:
        body = client.get("/api/v1/audit/actions").json()
        only_veto = client.get("/api/v1/audit/actions", params={"verdict": "veto"}).json()
        only_allow = client.get("/api/v1/audit/actions", params={"verdict": "allow"}).json()

    actions = body["actions"]
    assert len(actions) == 2
    for action in actions:
        assert set(action) == {
            "id",
            "ts",
            "tool",
            "args",
            "result",
            "conscience_verdict",
            "veto_reason",
            "goal_id",
            "success",
        }

    allow = next(a for a in actions if a["conscience_verdict"] == "allow")
    veto = next(a for a in actions if a["conscience_verdict"] == "veto")

    assert allow["tool"] == "noop"
    assert allow["args"] == {"message": "hello"}
    assert allow["result"] == {
        "success": True,
        "output": {"echo": "hello"},
        "summary": "noop echoed: 'hello'",
    }
    assert allow["veto_reason"] is None
    assert allow["goal_id"] == 1
    assert allow["success"] is True

    assert veto["result"] is None  # vetoed → never ran
    assert veto["veto_reason"] == "this is not who I am"
    assert veto["goal_id"] == 2
    assert veto["success"] is False

    # The verdict filter slices the trail.
    assert [a["conscience_verdict"] for a in only_veto["actions"]] == ["veto"]
    assert [a["conscience_verdict"] for a in only_allow["actions"]] == ["allow"]
