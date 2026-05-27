/**
 * Adapter for `GET /api/v1/self` — identity doc + latest reflections.
 *
 * ServerEnvelope source: `tests/fixtures/wire/self.json` (+ `.empty.json`, the
 * seed-v1 identity with empty concerns + no notes).
 */

import { get } from "./http";
import type { Identity, SelfNote, SelfView } from "./types";

export interface SelfEnvelope {
  identity: Identity;
  notes: SelfNote[];
}

export function adaptSelf(envelope: SelfEnvelope): SelfView {
  return {
    identity: envelope.identity,
    notes: envelope.notes ?? [],
  };
}

export async function fetchSelf(notesLimit?: number, signal?: AbortSignal): Promise<SelfView> {
  return adaptSelf(await get<SelfEnvelope>("/self", { notes_limit: notesLimit }, signal));
}
