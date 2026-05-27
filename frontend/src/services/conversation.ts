/**
 * Adapter for `POST /api/v1/input` — send Johnny a message.
 *
 * This is NOT request/response: the 202 ack carries only `{accepted,queue_depth}`,
 * NOT a reply. His reply emerges later as a thought on `/ws/consciousness` (he
 * *thinks about* your message — continuity, not a synchronous answer, SPEC §7).
 * The conversation panel shows "he heard you" and correlates the thought.
 *
 * ServerEnvelope source: `tests/fixtures/wire/input.json`.
 */

import { postJson } from "./http";
import type { InputAck } from "./types";

export interface InputEnvelope {
  accepted: boolean;
  queue_depth: number;
}

export interface InputRequestBody {
  text: string;
}

export function adaptInputAck(envelope: InputEnvelope): InputAck {
  return {
    accepted: envelope.accepted,
    queue_depth: envelope.queue_depth,
  };
}

export async function sendMessage(text: string, signal?: AbortSignal): Promise<InputAck> {
  const body: InputRequestBody = { text };
  return adaptInputAck(await postJson<InputEnvelope>("/input", body, signal));
}
