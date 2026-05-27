"""Secret redaction for anything that reaches the bus or the audit log.

``GET /api/v1/audit`` returns ``workspace_event`` payloads (and the action trail)
verbatim to any token-holder, and the live ``/ws/consciousness`` stream does too.
From Phase 6 on, tool results can carry credentials lifted from fetched content or
config (web/news/messaging), so a guard sits on **both** persistence paths and
strips secret-shaped strings *before* they're written or published:

* the Mind's bus — ``Workspace.broadcast`` redacts every ``workspace_event``;
* the Core's audit — ``AuditWriter.record`` redacts ``action_log`` ``args``/``result``.

Two independent guards on two paths is deliberate (FC-1): the Core can't trust the
Mind to have redacted, so it redacts its own writes. Both share this module, which
lives in ``foundation`` precisely so the import-isolated Core can use it without
importing ``brain``.

What's caught: dict values under a sensitive *key name* (``password``, ``token``,
``api_key``, …); string values matching a credential *shape* (Groq/OpenAI keys,
AWS ids, JWTs, ``Bearer`` tokens); and the exact known secret *values* loaded from
config (the ``.env`` Groq key, WS token, DB password) wherever they appear — so a
known secret can never slip through even in an unexpected position. Redaction is a
no-op for ordinary cognition payloads, which carry none of these.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from foundation.config import get_settings

REDACTION_MARKER = "[REDACTED]"

# Known secret values shorter than this aren't exact-matched, so a trivial/placeholder
# config value (or empty default) can't redact ordinary text everywhere.
_MIN_SECRET_LEN = 6

# A dict key whose lowercased name contains one of these marks its value as a secret,
# regardless of the value's shape. Kept specific so benign keys aren't caught (no bare
# "key"/"auth" — those match "mood_key"/"author").
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "authorization",
    "credential",
    "private_key",
    "client_secret",
    "auth_token",
)

# String values that look like credentials wherever they appear get redacted in place.
_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),  # Groq API key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style key
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),  # bearer tokens
)


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _known_secret_values() -> tuple[str, ...]:
    """The exact secret values to scrub wherever they appear (from config)."""
    settings = get_settings()
    candidates = (settings.groq_api_key, settings.ws_token, settings.postgres_password)
    return tuple(value for value in candidates if value and len(value) >= _MIN_SECRET_LEN)


def _redact_str(value: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret in value:
            value = value.replace(secret, REDACTION_MARKER)
    for pattern in _VALUE_PATTERNS:
        value = pattern.sub(REDACTION_MARKER, value)
    return value


def _redact_value(value: object, secrets: Sequence[str]) -> object:
    """Recursively redact a JSON-like value (str/dict/list/scalar), returning a copy."""
    if isinstance(value, str):
        return _redact_str(value, secrets)
    if isinstance(value, Mapping):
        return {
            key: (REDACTION_MARKER if _is_sensitive_key(key) else _redact_value(item, secrets))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def redact_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Return a redacted copy of a JSON-like payload before it's persisted/published.

    Scrubs sensitive-key values, credential-shaped strings, and known secret values
    at any depth. A no-op for payloads with none of those (ordinary cognition data).
    """
    secrets = _known_secret_values()
    return {
        key: (REDACTION_MARKER if _is_sensitive_key(key) else _redact_value(value, secrets))
        for key, value in payload.items()
    }
