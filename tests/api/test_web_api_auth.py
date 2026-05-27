"""TC-5a.1 — the shared-token gate protects every ``/api/v1`` route + health redaction.

With a non-blank ``ws_token``, every ``/api/v1/*`` route must reject a missing or
wrong token with **401** (before any data), accept the correct token (via
``Authorization: Bearer`` *or* ``X-Token``), and a **blank** token must open the
gate (dev). ``/api/health`` is redacted for unauthenticated callers (bare
``{"status": ...}`` + 200/503) and detailed for authenticated ones (the Phase-0
advisory). DB+Redis backed → in-network via ``./ctl.sh test``.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers.web_api import build_api_app

TOKEN = "s3cret"

# (method, path, success_status) for the full guarded surface. POST /input is the
# only write; the rest are reads. Bodies for POST are minimal+valid so the ONLY
# thing that can 401 is the gate (it runs before the body is processed).
_ROUTES: list[tuple[str, str, int]] = [
    ("GET", "/api/v1/state", 200),
    ("GET", "/api/v1/thoughts", 200),
    ("GET", "/api/v1/audit", 200),
    ("GET", "/api/v1/memory/episodes", 200),
    ("GET", "/api/v1/memory/facts", 200),
    ("GET", "/api/v1/goals", 200),
    ("GET", "/api/v1/sleeps", 200),
    ("GET", "/api/v1/self", 200),
    ("POST", "/api/v1/input", 202),
]


def _request(client: TestClient, method: str, path: str, headers: dict[str, str]) -> httpx.Response:
    if method == "POST":
        return client.post(path, json={"text": "hello"}, headers=headers)
    return client.get(path, headers=headers)


@pytest.mark.parametrize(("method", "path", "ok_status"), _ROUTES)
def test_route_rejects_missing_and_wrong_token(
    _migrated_test_db: None, method: str, path: str, ok_status: int
) -> None:
    """No token and a wrong token → 401 with ``WWW-Authenticate: Bearer`` and no data body."""
    app = build_api_app(ws_token=TOKEN)
    with TestClient(app) as client:
        no_token = _request(client, method, path, {})
        wrong = _request(client, method, path, {"X-Token": "nope"})

    for resp in (no_token, wrong):
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") == "Bearer"
        # 401 fires before the handler → only the error envelope, never seeded data.
        assert set(resp.json()) == {"detail"}
        assert TOKEN not in resp.text  # the expected token is never echoed


@pytest.mark.parametrize(("method", "path", "ok_status"), _ROUTES)
def test_route_admits_correct_token_via_bearer_and_x_token(
    _migrated_test_db: None, method: str, path: str, ok_status: int
) -> None:
    """The correct token is admitted as ``Authorization: Bearer`` AND as ``X-Token``."""
    app = build_api_app(ws_token=TOKEN)
    with TestClient(app) as client:
        bearer = _request(client, method, path, {"Authorization": f"Bearer {TOKEN}"})
        x_token = _request(client, method, path, {"X-Token": TOKEN})

    assert bearer.status_code == ok_status
    assert x_token.status_code == ok_status


@pytest.mark.parametrize(("method", "path", "ok_status"), _ROUTES)
def test_blank_token_opens_the_gate(
    _migrated_test_db: None, method: str, path: str, ok_status: int
) -> None:
    """A blank ``ws_token`` disables the gate (local dev) — no header needed."""
    app = build_api_app(ws_token="")
    with TestClient(app) as client:
        resp = _request(client, method, path, {})
    assert resp.status_code == ok_status


def test_health_redacted_for_unauthenticated_detailed_for_authenticated(
    _migrated_test_db: None,
) -> None:
    """Unauthenticated ``/api/health`` → bare ``{"status": ...}``; authenticated → full topology."""
    app = build_api_app(ws_token=TOKEN)
    with TestClient(app) as client:
        bare = client.get("/api/health")
        detailed = client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})

    # Unauthenticated: a bare status + a real readiness code, NO per-dependency detail.
    assert bare.status_code in (200, 503)
    assert set(bare.json()) == {"status"}
    assert bare.json()["status"] in {"ok", "degraded", "unhealthy"}

    # Authenticated: the full report (status code matches the same readiness).
    assert detailed.status_code == bare.status_code
    body = detailed.json()
    assert "dependencies" in body
    assert {"status", "service", "environment", "timestamp"} <= set(body)
