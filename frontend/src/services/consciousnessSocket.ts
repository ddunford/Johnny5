/**
 * `consciousnessSocket` — wires `/ws/consciousness` into the consciousness store.
 *
 * A single shared connection per session, reference-counted so it opens when the
 * first consumer attaches and closes when the last detaches. The server replays
 * recent thoughts on connect (backfill); the store dedupes by id so a reconnect's
 * replay doesn't duplicate. A 1008 (token rejected) bounces to the gate.
 */

import { reject } from "@/auth/tokenStore";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import { ReconnectingSocket, type WebSocketLike } from "./reconnectingSocket";
import { adaptThoughtFrame, isThoughtFrame } from "./wsFrames";

let socket: ReconnectingSocket | null = null;
let refCount = 0;

/** Test seam: override the WebSocket factory the next connection uses. */
let createWebSocket: ((url: string) => WebSocketLike) | undefined;
export function __setConsciousnessSocketFactory(
  factory: ((url: string) => WebSocketLike) | undefined,
): void {
  createWebSocket = factory;
}

function open(): void {
  socket = new ReconnectingSocket({
    path: "/ws/consciousness",
    createWebSocket,
    onFrame: (frame) => {
      if (isThoughtFrame(frame)) {
        useConsciousnessStore.getState().addThought(adaptThoughtFrame(frame));
      }
    },
    onStatus: (status) => useConsciousnessStore.getState().setStatus(status),
    onAuthReject: reject,
  });
  socket.start();
}

/**
 * Attach to the stream. Returns a release fn (call on unmount). The first attach
 * resets the store (fresh session) and opens; the last release closes.
 */
export function acquireConsciousnessStream(): () => void {
  refCount += 1;
  if (refCount === 1) {
    useConsciousnessStore.getState().reset();
    open();
  }
  return () => {
    refCount = Math.max(0, refCount - 1);
    if (refCount === 0 && socket) {
      socket.stop();
      socket = null;
    }
  };
}
