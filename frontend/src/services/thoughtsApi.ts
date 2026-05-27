/**
 * Adapter for `GET /api/v1/thoughts` — the REST backfill of recent thoughts (the
 * live feed itself comes over `/ws/consciousness`).
 *
 * ServerEnvelope source: `tests/fixtures/wire/thoughts.json` (+ `.empty.json`).
 */

import { get } from "./http";
import type { Thought } from "./types";

export interface ThoughtsEnvelope {
  thoughts: Thought[];
}

/** Project to the thought list (empty array on a fresh Mind). */
export function adaptThoughts(envelope: ThoughtsEnvelope): Thought[] {
  return envelope.thoughts ?? [];
}

export async function fetchThoughts(limit?: number, signal?: AbortSignal): Promise<Thought[]> {
  return adaptThoughts(await get<ThoughtsEnvelope>("/thoughts", { limit }, signal));
}
