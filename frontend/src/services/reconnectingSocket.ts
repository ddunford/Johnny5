/**
 * A small reconnecting WebSocket wrapper shared by both live streams.
 *
 * Responsibilities:
 *   • Authenticate the handshake with `?token=` (browsers can't set WS headers —
 *     known interim limitation; the token is read from {@link tokenStore} at each
 *     connect, never logged).
 *   • Backfill is server-driven: the 5a endpoints replay recent events on connect,
 *     so the client just receives them as ordinary frames (the stores dedupe).
 *   • Auto-reconnect with capped exponential backoff on a transient drop.
 *   • A **1008** close (token rejected) is terminal — it calls `onAuthReject`
 *     (→ {@link tokenStore.reject}, bouncing to the gate) and does NOT reconnect.
 *
 * The `createWebSocket` / `getToken` seams make it unit-testable without a real
 * socket (jsdom has no WebSocket).
 */

import { getToken as defaultGetToken } from "@/auth/tokenStore";
import type { ConnectionStatus } from "./connectionStatus";

/** The subset of the WebSocket API this wrapper uses (so tests can fake it). */
export interface WebSocketLike {
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
}

export interface ReconnectingSocketOptions {
  /** WS path, e.g. `/ws/consciousness`. */
  path: string;
  /** Called with each parsed JSON frame. */
  onFrame: (frame: unknown) => void;
  /** Called whenever the connection status changes. */
  onStatus: (status: ConnectionStatus) => void;
  /** Called on a 1008 (token rejected) close — terminal, no reconnect. */
  onAuthReject: () => void;
  createWebSocket?: (url: string) => WebSocketLike;
  getToken?: () => string | null;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
}

/** WS close code 1008 = policy violation — our backend uses it for a bad token. */
const POLICY_VIOLATION = 1008;

function defaultCreateWebSocket(url: string): WebSocketLike {
  return new WebSocket(url) as unknown as WebSocketLike;
}

function buildWsUrl(path: string, token: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}?token=${encodeURIComponent(token)}`;
}

export class ReconnectingSocket {
  private readonly options: Required<
    Pick<ReconnectingSocketOptions, "createWebSocket" | "getToken" | "initialBackoffMs" | "maxBackoffMs">
  > &
    ReconnectingSocketOptions;

  private socket: WebSocketLike | null = null;
  private stopped = false;
  private backoffMs: number;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: ReconnectingSocketOptions) {
    // Coalesce each defaulted field AFTER the spread: callers (the socket clients)
    // pass `createWebSocket: <module var>` which is `undefined` in production (the
    // factory is only set by the test seam). Object spread does NOT skip
    // `undefined`, so a plain `...options` over the defaults would clobber them
    // with `undefined` → `createWebSocket is not a function` on connect. `??`
    // restores the real `new WebSocket(...)` default whenever a value is absent.
    this.options = {
      ...options,
      createWebSocket: options.createWebSocket ?? defaultCreateWebSocket,
      getToken: options.getToken ?? defaultGetToken,
      initialBackoffMs: options.initialBackoffMs ?? 500,
      maxBackoffMs: options.maxBackoffMs ?? 8000,
    };
    this.backoffMs = this.options.initialBackoffMs;
  }

  /** Open the connection (idempotent — calling twice does nothing). */
  start(): void {
    this.stopped = false;
    if (this.socket) {
      return;
    }
    this.connect();
  }

  /** Close permanently — no further reconnects. */
  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.close();
    }
    this.options.onStatus("closed");
  }

  private connect(): void {
    const token = this.options.getToken();
    if (!token) {
      // No token → nothing to attach to; the gate will be showing anyway.
      this.options.onStatus("closed");
      return;
    }

    this.options.onStatus(this.backoffMs === this.options.initialBackoffMs ? "connecting" : "reconnecting");

    let socket: WebSocketLike;
    try {
      socket = this.options.createWebSocket(buildWsUrl(this.options.path, token));
    } catch {
      // Construction failed (e.g. WebSocket unavailable). Degrade gracefully and
      // retry with backoff — a dead stream must NOT throw out of the effect and
      // take the whole viewer down ("a dead panel must not kill the live view").
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.backoffMs = this.options.initialBackoffMs;
      this.options.onStatus("open");
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      try {
        this.options.onFrame(JSON.parse(event.data));
      } catch {
        // Ignore a malformed frame rather than tear down the stream.
      }
    };

    socket.onclose = (event) => {
      this.socket = null;
      if (this.stopped) {
        return;
      }
      if (event.code === POLICY_VIOLATION) {
        // Token rejected mid-stream — terminal. Bounce to the gate.
        this.stopped = true;
        this.options.onAuthReject();
        this.options.onStatus("closed");
        return;
      }
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      // A close event follows; reconnection is handled there.
    };
  }

  private scheduleReconnect(): void {
    this.options.onStatus("reconnecting");
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.stopped) {
        this.connect();
      }
    }, this.backoffMs);
    this.backoffMs = Math.min(this.backoffMs * 2, this.options.maxBackoffMs);
  }
}
