/**
 * Adapter for `GET /api/v1/state` — the REST state snapshot used for the
 * dashboard's initial paint before the `/ws/state` stream takes over (FC-8).
 *
 * ServerEnvelope source: `tests/fixtures/wire/state.json` (+ `state.empty.json`).
 * The REST snapshot is the bare state payload (NO `{type,id,ts}` wrapper — that
 * wrapper exists only on the WS frame, handled by `stateSocket`).
 */

import { get } from "./http";
import type { Drive, Mood, SleepStatus, StateGoal, StateView } from "./types";

export interface StateEnvelope {
  tick: number;
  drives: Drive[];
  mood: Mood | null;
  goals: StateGoal[];
  interval: number;
  sleep: SleepStatus;
}

const DEFAULT_SLEEP: SleepStatus = { asleep: false, full_agency: true, last: null };

/** Project the REST snapshot into the shared {@link StateView}, defensively. */
export function adaptState(envelope: StateEnvelope): StateView {
  return {
    tick: envelope.tick ?? 0,
    drives: envelope.drives ?? [],
    mood: envelope.mood ?? null,
    goals: envelope.goals ?? [],
    interval: envelope.interval ?? 0,
    sleep: envelope.sleep ?? DEFAULT_SLEEP,
  };
}

export async function fetchState(signal?: AbortSignal): Promise<StateView> {
  return adaptState(await get<StateEnvelope>("/state", undefined, signal));
}
