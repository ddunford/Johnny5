"""The Core's append-only action audit writer (``SPEC §9.3`` / FC-1).

Every action Johnny's dispatch handles — allowed or vetoed — is written here, to
``action_log``, as one immutable row. This is the audit safeguard that makes
"total observability" survive even a *misbehaving* Mind: the record is written by
the **Core**, and the Mind has no path to rewrite or delete it.

**Import-isolated (FC-1).** Like ``core/governors.py``, this module imports only
``foundation`` + SQLAlchemy — never ``brain``. It writes ``action_log`` by *table
name*, so the Core never depends on a Mind ORM model, and it takes **primitives**
(not a Mind type), so it satisfies the dispatch's ``AuditSink`` seam structurally
without importing it. The Mind injects an instance and calls ``record`` — it can
*use* the Core writer, but it cannot reach into the Core or bypass it.

**Append-only by construction.** The only operation here is an ``INSERT``. There
is deliberately no update or delete method — nothing in the Core or the Mind can
amend or erase a logged action through this writer (and the Mind's read model,
``brain/effectors/action_log.py``, is read-only too). The trail only grows.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from foundation.db import session_scope
from foundation.redaction import redact_payload

# The physical table the writer appends to. Referenced by name (FC-1) so the Core
# never imports the Mind's ORM model; must match
# ``brain.effectors.action_log.ACTION_LOG_TABLE``.
ACTION_LOG_TABLE = "action_log"

_INSERT_SQL = text(
    # Constant table name (not user input) — interpolated once, params are bound.
    f"INSERT INTO {ACTION_LOG_TABLE} "  # noqa: S608
    "(tool, args, result, conscience_verdict, veto_reason, goal_id, success) "
    "VALUES (:tool, CAST(:args AS JSONB), CAST(:result AS JSONB), "
    ":conscience_verdict, :veto_reason, :goal_id, :success)"
)


class AuditWriter:
    """Append-only writer for ``action_log`` (FC-1). Construct once; inject as the sink.

    Satisfies the dispatch's ``AuditSink`` protocol structurally (primitive kwargs),
    so the Mind routes every action_log write through the Core here without the
    Core ever importing the Mind.
    """

    async def record(
        self,
        *,
        tool: str,
        args: dict[str, object],
        result: dict[str, object] | None,
        conscience_verdict: str,
        veto_reason: str | None,
        goal_id: int | None,
        success: bool,
    ) -> None:
        """Append one immutable ``action_log`` row.

        ``result`` is the tool's structured output, or ``None`` for a vetoed action
        (it never ran). ``ts``/``id`` come from the table's server defaults. Each
        call is its own committed transaction — the durable record can't be lost to
        a later rollback in the caller.

        ``args`` and ``result`` are **redacted before persistence** (no-secrets-on-
        the-bus): a tool's args/result can carry credentials, and ``/api/v1/audit``
        is world-readable to a token-holder. The Core redacts its own writes rather
        than trusting the Mind to have done so (FC-1).
        """
        safe_args = redact_payload(args)
        safe_result = redact_payload(result) if result is not None else None
        async with session_scope() as session:
            await session.execute(
                _INSERT_SQL,
                {
                    "tool": tool,
                    "args": json.dumps(safe_args),
                    "result": json.dumps(safe_result) if safe_result is not None else None,
                    "conscience_verdict": conscience_verdict,
                    "veto_reason": veto_reason,
                    "goal_id": goal_id,
                    "success": success,
                },
            )
