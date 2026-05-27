"""TC-6a.7 — no secret reaches the bus or the audit log (the no-secrets guard).

Two layers (FC-1): the Mind's bus (``Workspace.broadcast`` → ``workspace_event``)
and the Core's audit (``AuditWriter.record`` → ``action_log``) each redact a
payload *before persistence*. We test the pure ``redact_payload`` projection and
then both real write paths end-to-end (DB-/Redis-backed, in-network):

* sensitive *key names* (password/token/api_key/…) → the value is replaced;
* credential-*shaped* strings (Groq/OpenAI keys, AWS ids, JWTs, Bearer tokens)
  → redacted in place, at any depth;
* the exact known secret *values* from config (the ``.env`` Groq key / WS token /
  DB password) → scrubbed wherever they appear, even under a benign key;
* ordinary cognition payloads pass through untouched (a no-op).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from helpers.web_api import build_api_app
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from brain.workspace import Workspace, WorkspaceEvent
from core.audit import AuditWriter
from foundation.config import get_settings
from foundation.db import session_scope
from foundation.redaction import REDACTION_MARKER, redact_payload

# Distinctive sentinels so an assertion can prove the exact value never survived.
_GROQ_SHAPED = "gsk_" + "S3CR3Tvalue" + "A" * 24
_OPENAI_SHAPED = "sk-" + "T0KENvalue" + "B" * 24
_AWS_SHAPED = "AKIA" + "1234567890ABCDEF"
_BEARER = "Bearer abc.def-ghi_12345"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdEFGH_signature-part"


# ── pure projection ──────────────────────────────────────────────────────────


def test_sensitive_key_value_is_replaced_regardless_of_shape() -> None:
    out = redact_payload({"password": "plain-but-secret", "api_key": "also-secret"})
    assert out == {"password": REDACTION_MARKER, "api_key": REDACTION_MARKER}


def test_credential_shaped_values_are_redacted_in_place() -> None:
    out = redact_payload(
        {
            "note": f"the key is {_GROQ_SHAPED} ok",
            "other": _OPENAI_SHAPED,
            "aws": _AWS_SHAPED,
            "auth_header": _BEARER,
            "jwt": _JWT,
        }
    )
    blob = json.dumps(out)
    for sentinel in (_GROQ_SHAPED, _OPENAI_SHAPED, _AWS_SHAPED, _BEARER, _JWT):
        assert sentinel not in blob
    assert REDACTION_MARKER in out["note"]  # type: ignore[operator]


def test_redaction_recurses_into_nested_dicts_and_lists() -> None:
    out = redact_payload(
        {"outer": {"token": "deep-secret", "items": [{"note": _GROQ_SHAPED}, "clean"]}}
    )
    blob = json.dumps(out)
    assert "deep-secret" not in blob
    assert _GROQ_SHAPED not in blob
    assert REDACTION_MARKER in blob


def test_ordinary_payload_is_unchanged() -> None:
    payload = {"thought": "I wonder what Dan is up to", "mood": {"valence": 0.4}, "tags": ["idle"]}
    assert redact_payload(payload) == payload


def test_benign_keys_are_not_over_redacted() -> None:
    # "author"/"mood_key" must not trip the (specific) sensitive-key matcher.
    payload = {"author": "Johnny", "mood_key": "calm"}
    assert redact_payload(payload) == payload


# ── real write paths (DB-/Redis-backed) ──────────────────────────────────────


async def _action_log_rows() -> list[dict[str, object]]:
    async with session_scope() as session:
        result = await session.execute(
            text("SELECT tool, args, result FROM action_log ORDER BY id")
        )
        return [dict(row._mapping) for row in result]


async def _event_payloads() -> list[object]:
    async with session_scope() as session:
        result = await session.execute(text("SELECT payload FROM workspace_event ORDER BY id"))
        return [row._mapping["payload"] for row in result]


async def test_audit_writer_redacts_args_and_result_before_persistence(
    effector_db: AsyncEngine,
) -> None:
    await AuditWriter().record(
        tool="noop",
        args={"api_key": _GROQ_SHAPED, "note": f"bearer {_BEARER}"},
        result={"echo": _OPENAI_SHAPED, "password": "leaked-pw"},
        conscience_verdict="allow",
        veto_reason=None,
        goal_id=1,
        success=True,
    )

    rows = await _action_log_rows()
    blob = json.dumps(rows[0])
    for sentinel in (_GROQ_SHAPED, _OPENAI_SHAPED, "leaked-pw"):
        assert sentinel not in blob
    assert REDACTION_MARKER in blob


async def test_broadcast_redacts_secrets_on_the_bus(bus: Workspace) -> None:
    await bus.broadcast(
        WorkspaceEvent(
            module="effectors",
            type="action.dispatched",
            payload={"token": "bus-secret-value", "result": {"body": _GROQ_SHAPED}},
        )
    )

    payloads = await _event_payloads()
    blob = json.dumps(payloads[0])
    assert "bus-secret-value" not in blob
    assert _GROQ_SHAPED not in blob
    assert REDACTION_MARKER in blob


async def test_known_env_secret_values_never_reach_either_sink(bus: Workspace) -> None:
    """The exact configured secrets are scrubbed wherever they appear (even benign keys)."""
    settings = get_settings()
    known = [
        value
        for value in (settings.groq_api_key, settings.ws_token, settings.postgres_password)
        if value and len(value) >= 6
    ]
    if not known:
        pytest.skip("no known secret values configured in this test env")

    # Hide each known secret inside an innocuously-named field.
    payload: dict[str, object] = {f"field_{i}": f"prefix-{v}-suffix" for i, v in enumerate(known)}

    # Bus path.
    await bus.broadcast(WorkspaceEvent(module="x", type="t", payload=dict(payload)))
    bus_blob = json.dumps(await _event_payloads())
    for value in known:
        assert value not in bus_blob

    # Audit path.
    await AuditWriter().record(
        tool="noop",
        args=dict(payload),
        result=dict(payload),
        conscience_verdict="allow",
        veto_reason=None,
        goal_id=None,
        success=True,
    )
    audit_blob = json.dumps(await _action_log_rows())
    for value in known:
        assert value not in audit_blob


# ── redaction holds through the real durable read endpoint ───────────────────


async def _seed_secret_action(_runtime: object) -> None:
    """A dispatched action whose args/result carry secret-shaped strings + a secret key."""
    await AuditWriter().record(
        tool="noop",
        args={"api_key": _GROQ_SHAPED, "note": "ordinary"},
        result={"echo": _OPENAI_SHAPED, "password": "leaked-pw"},
        conscience_verdict="allow",
        veto_reason=None,
        goal_id=1,
        success=True,
    )


def test_audit_actions_endpoint_never_serves_a_secret(_migrated_test_db: None) -> None:
    """The token-readable GET /api/v1/audit/actions can't leak a secret (TC-6a.7).

    Redacted at write *and* re-run through the guard on read (defense in depth) —
    the marker is idempotent, so the response carries [REDACTED], never the secret.
    """
    app = build_api_app(ws_token="", seed=_seed_secret_action)
    with TestClient(app) as client:
        body = client.get("/api/v1/audit/actions").json()

    blob = json.dumps(body)
    for sentinel in (_GROQ_SHAPED, _OPENAI_SHAPED, "leaked-pw"):
        assert sentinel not in blob
    assert REDACTION_MARKER in blob
