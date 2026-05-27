/**
 * Adapter for `GET /api/v1/audit` — the bus/event log (optional `type` filter).
 *
 * ServerEnvelope source: `tests/fixtures/wire/audit.json` (+ `.empty.json`).
 */

import { get } from "./http";
import type { AuditEvent } from "./types";

export interface AuditEnvelope {
  events: AuditEvent[];
}

export function adaptAudit(envelope: AuditEnvelope): AuditEvent[] {
  return envelope.events ?? [];
}

export interface AuditQuery {
  type?: string;
  limit?: number;
}

export async function fetchAudit(
  { type, limit }: AuditQuery = {},
  signal?: AbortSignal,
): Promise<AuditEvent[]> {
  return adaptAudit(await get<AuditEnvelope>("/audit", { type, limit }, signal));
}
