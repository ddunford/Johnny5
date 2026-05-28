/**
 * Adapter for `GET /api/v1/audit/actions` — the durable, Core-written action_log
 * trail (the trustworthy record that survives a misbehaving Mind, SPEC §9.3).
 * Distinct from `/audit` (the Mind's live bus feed in auditApi.ts): this is the
 * append-only effector-action trail the Core appends, with args/result redacted
 * on the way out.
 *
 * ServerEnvelope source: the ACTUAL shape of `johnny/api/v1/schemas.py`
 * `ActionAuditResponse` / `ActionAudit`, captured to `tests/fixtures/wire/
 * audit_actions.json` (+ `.empty.json`). A hand-authored interface is a CLAIM;
 * only the fixture-fed contract test (adapters.contract.test.ts) proves the shape.
 */

import { get } from "./http";

/**
 * One durable action_log row. Mirrors `johnny/api/v1/schemas.py:ActionAudit`
 * VERBATIM — a server-side rename here breaks the contract test, not production.
 * `result` is null on a veto (the tool never ran); `veto_reason` is set only on a
 * veto; args/result values are already `[REDACTED]` server-side where sensitive.
 */
export interface ActionAudit {
  id: number | null;
  ts: string | null;
  tool: string;
  args: Record<string, unknown>;
  result: Record<string, unknown> | null;
  conscience_verdict: string;
  veto_reason: string | null;
  goal_id: number | null;
  success: boolean;
}

/** The `GET /api/v1/audit/actions` envelope (`ActionAuditResponse`). */
export interface ActionAuditEnvelope {
  actions: ActionAudit[];
}

/** Project the envelope to the action list — empty `{actions:[]}` is the default
 * (a fresh trail before any tool has run), so this never assumes a populated array. */
export function adaptAuditActions(envelope: ActionAuditEnvelope): ActionAudit[] {
  return envelope.actions ?? [];
}

export interface ActionAuditQuery {
  /** Filter to a single verdict; omitted = all. */
  verdict?: "allow" | "veto";
  limit?: number;
}

export async function fetchAuditActions(
  { verdict, limit }: ActionAuditQuery = {},
  signal?: AbortSignal,
): Promise<ActionAudit[]> {
  return adaptAuditActions(
    await get<ActionAuditEnvelope>("/audit/actions", { verdict, limit }, signal),
  );
}
