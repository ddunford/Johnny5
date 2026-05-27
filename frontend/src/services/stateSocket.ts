/**
 * `stateSocket` — wires `/ws/state` into the state store.
 *
 * The dashboard paints first from the REST snapshot (`GET /api/v1/state`) and then
 * this live stream takes over (FC-8): each frame is the whole current state, so it
 * replaces the snapshot. Reference-counted single connection; 1008 → gate.
 */

import { reject } from "@/auth/tokenStore";
import { useStateStore } from "@/stores/stateStore";
import { ReconnectingSocket, type WebSocketLike } from "./reconnectingSocket";
import { adaptStateFrame, isStateFrame } from "./wsFrames";

let socket: ReconnectingSocket | null = null;
let refCount = 0;

/** Test seam: override the WebSocket factory the next connection uses. */
let createWebSocket: ((url: string) => WebSocketLike) | undefined;
export function __setStateSocketFactory(
  factory: ((url: string) => WebSocketLike) | undefined,
): void {
  createWebSocket = factory;
}

function open(): void {
  socket = new ReconnectingSocket({
    path: "/ws/state",
    createWebSocket,
    onFrame: (frame) => {
      if (isStateFrame(frame)) {
        useStateStore.getState().setSnapshot(adaptStateFrame(frame));
      }
    },
    onStatus: (status) => useStateStore.getState().setStatus(status),
    onAuthReject: reject,
  });
  socket.start();
}

/**
 * Attach to the state stream. Returns a release fn. The first attach resets the
 * store (fresh session) and opens; the last release closes.
 */
export function acquireStateStream(): () => void {
  refCount += 1;
  if (refCount === 1) {
    useStateStore.getState().reset();
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
