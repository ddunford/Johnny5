/**
 * Adapters for the two WebSocket frame shapes. Unlike the REST envelopes, every
 * WS frame carries a `{type,id,ts,…}` wrapper (the bus-event envelope). These
 * project the frames into the same domain types the REST adapters produce, so a
 * panel reads one shape whether the data arrived by snapshot or by stream (FC-8).
 *
 * ServerEnvelope sources:
 *   `tests/fixtures/wire/ws_consciousness.json` (+ `.empty.json`) — thought frames
 *   `tests/fixtures/wire/ws_state.json`         (+ `.empty.json`) — a state frame
 */

import { adaptState, type StateEnvelope } from "./stateApi";
import type { StateView, Thought } from "./types";

/** A `/ws/consciousness` frame: a thought wrapped in the bus envelope. */
export interface ThoughtFrame {
  type: "thought";
  id: number;
  ts: string | null;
  text: string;
}

/** A `/ws/state` frame: the state payload wrapped in the bus envelope. */
export interface StateFrame extends StateEnvelope {
  type: "state";
  id: number;
  ts: string | null;
}

/** Narrow an arbitrary parsed frame to a thought frame. */
export function isThoughtFrame(frame: unknown): frame is ThoughtFrame {
  return (
    typeof frame === "object" &&
    frame !== null &&
    (frame as { type?: unknown }).type === "thought"
  );
}

/** Narrow an arbitrary parsed frame to a state frame. */
export function isStateFrame(frame: unknown): frame is StateFrame {
  return (
    typeof frame === "object" &&
    frame !== null &&
    (frame as { type?: unknown }).type === "state"
  );
}

/** Project a thought frame to a {@link Thought} (drops the `type`; tolerates null ts). */
export function adaptThoughtFrame(frame: ThoughtFrame): Thought {
  return {
    id: frame.id,
    ts: frame.ts ?? "",
    text: frame.text ?? "",
  };
}

/** Project a state frame to a {@link StateView} (drops the `{type,id,ts}` wrapper). */
export function adaptStateFrame(frame: StateFrame): StateView {
  return adaptState(frame);
}
