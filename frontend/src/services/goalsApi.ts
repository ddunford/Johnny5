/**
 * Adapter for `GET /api/v1/goals` — active + recently-resolved goals.
 *
 * ServerEnvelope source: `tests/fixtures/wire/goals.json` (+ `.empty.json`).
 */

import { get } from "./http";
import type { Goal, GoalsView } from "./types";

export interface GoalsEnvelope {
  active: Goal[];
  recent: Goal[];
}

export function adaptGoals(envelope: GoalsEnvelope): GoalsView {
  return {
    active: envelope.active ?? [],
    recent: envelope.recent ?? [],
  };
}

export async function fetchGoals(limit?: number, signal?: AbortSignal): Promise<GoalsView> {
  return adaptGoals(await get<GoalsEnvelope>("/goals", { limit }, signal));
}
