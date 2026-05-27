"""HTTP shared-token gate for ``/api/v1`` (interim single-token model).

This MIRRORS the WebSocket gate in ``johnny.api.ws._ws_authorised`` — the same
single ``WS_TOKEN`` protects both surfaces (CLAUDE.md: "single-token /
Traefik basic-auth gate, no user system"). A later phase can swap the body of the
dependency for a session / basic-auth scheme without touching the routes that
depend on it.

Rules (identical to the WS gate):

* A **blank** ``ws_token`` disables the gate — local-dev convenience.
* Otherwise the caller must present the token as ``Authorization: Bearer <token>``
  or the ``X-Token`` header.
* The comparison is **constant-time** (``secrets.compare_digest``) and the token
  is **never logged**.

``require_token`` is the raising dependency applied to the whole ``/api/v1``
router; ``token_is_valid`` is the non-raising predicate ``/api/health`` uses to
decide whether to reveal its per-dependency topology (the Phase-0 advisory).
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from foundation.config import Settings

_BEARER_PREFIX = "bearer "
# Header used to surface the auth scheme on a 401 (lets a client know to retry).
_WWW_AUTHENTICATE = "Bearer"


def _presented_token(request: Request) -> str:
    """Extract the token from ``Authorization: Bearer <t>`` or the ``X-Token`` header."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith(_BEARER_PREFIX):
        return authorization[len(_BEARER_PREFIX) :].strip()
    return request.headers.get("x-token", "")


def token_is_valid(request: Request, settings: Settings) -> bool:
    """Whether the request carries a valid token (or the gate is dev-open).

    Non-raising — ``/api/health`` calls this to choose its detail level. A blank
    ``ws_token`` means the gate is disabled and every caller is treated as
    authorised (mirrors the WS gate). Constant-time compared; never logs the token.
    """
    expected = settings.ws_token
    if not expected:
        return True
    return secrets.compare_digest(_presented_token(request), expected)


async def require_token(request: Request) -> None:
    """FastAPI dependency — reject with 401 unless a valid token is presented.

    Reads the live settings off ``request.app.state.settings`` (the same source the
    WS gate uses), so a test app can set its own token. Applied at the ``/api/v1``
    router level, so it guards every route uniformly with no per-route opt-out.
    """
    settings: Settings = request.app.state.settings
    if not token_is_valid(request, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing token",
            headers={"WWW-Authenticate": _WWW_AUTHENTICATE},
        )
