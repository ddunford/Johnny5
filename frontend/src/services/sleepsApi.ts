/**
 * Adapter for `GET /api/v1/sleeps` — completed sleep/consolidation logs.
 *
 * ServerEnvelope source: `tests/fixtures/wire/sleeps.json` (+ `.empty.json`).
 */

import { get } from "./http";
import type { SleepLog } from "./types";

export interface SleepsEnvelope {
  sleeps: SleepLog[];
}

export function adaptSleeps(envelope: SleepsEnvelope): SleepLog[] {
  return envelope.sleeps ?? [];
}

export async function fetchSleeps(limit?: number, signal?: AbortSignal): Promise<SleepLog[]> {
  return adaptSleeps(await get<SleepsEnvelope>("/sleeps", { limit }, signal));
}
