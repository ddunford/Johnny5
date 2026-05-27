import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReconnectingSocket, type WebSocketLike } from "./reconnectingSocket";
import {
  __setConsciousnessSocketFactory,
  acquireConsciousnessStream,
} from "./consciousnessSocket";
import { __setStateSocketFactory, acquireStateStream } from "./stateSocket";
import { adaptStateFrame, adaptThoughtFrame } from "./wsFrames";
import type { StateFrame, ThoughtFrame } from "./wsFrames";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import { useStateStore } from "@/stores/stateStore";
import { getToken, setToken } from "@/auth/tokenStore";
import { loadWire } from "@/test/wireFixtures";

class FakeWebSocket implements WebSocketLike {
  url: string;
  closed = false;
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number; reason?: string }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }
  close(): void {
    this.closed = true;
  }
  emitOpen(): void {
    this.onopen?.({});
  }
  emitFrame(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
  emitClose(code: number): void {
    this.onclose?.({ code });
  }
}

describe("WS frame adapters · fixtures", () => {
  it("adaptThoughtFrame drops the wrapper (ws_consciousness)", () => {
    const frames = loadWire<ThoughtFrame[]>("ws_consciousness");
    const thought = adaptThoughtFrame(frames[0]);
    expect(thought).toEqual({
      id: frames[0].id,
      ts: frames[0].ts,
      text: frames[0].text,
    });
    expect("type" in thought).toBe(false);
    expect(loadWire<ThoughtFrame[]>("ws_consciousness.empty")).toEqual([]);
  });

  it("adaptStateFrame projects the frame payload (ws_state populated + empty)", () => {
    const populated = adaptStateFrame(loadWire<StateFrame>("ws_state"));
    expect(populated.drives).toHaveLength(7);
    expect(populated.mood?.descriptor).toContain("calm");
    expect(populated.sleep.last?.self_model_version).toBe(2);

    const empty = adaptStateFrame(loadWire<StateFrame>("ws_state.empty"));
    expect(empty.mood).toBeNull();
    expect(empty.goals).toEqual([]);
    expect(empty.sleep.last).toBeNull();
  });
});

describe("ReconnectingSocket", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("opens with the token in the query string and reports open", () => {
    const created: FakeWebSocket[] = [];
    const statuses: string[] = [];
    const socket = new ReconnectingSocket({
      path: "/ws/x",
      getToken: () => "tok",
      createWebSocket: (url) => {
        const ws = new FakeWebSocket(url);
        created.push(ws);
        return ws;
      },
      onFrame: () => {},
      onStatus: (s) => statuses.push(s),
      onAuthReject: () => {},
    });
    socket.start();
    expect(created[0].url).toContain("/ws/x");
    expect(created[0].url).toContain("token=tok");
    created[0].emitOpen();
    expect(statuses).toContain("open");
    socket.stop();
  });

  it("reconnects after a transient (non-1008) close with backoff", () => {
    const created: FakeWebSocket[] = [];
    const socket = new ReconnectingSocket({
      path: "/ws/x",
      getToken: () => "tok",
      initialBackoffMs: 500,
      createWebSocket: (url) => {
        const ws = new FakeWebSocket(url);
        created.push(ws);
        return ws;
      },
      onFrame: () => {},
      onStatus: () => {},
      onAuthReject: () => {},
    });
    socket.start();
    created[0].emitOpen();
    created[0].emitClose(1006);
    expect(created).toHaveLength(1);
    vi.advanceTimersByTime(500);
    expect(created).toHaveLength(2);
    socket.stop();
  });

  it("does NOT reconnect on a 1008 close and fires onAuthReject", () => {
    const created: FakeWebSocket[] = [];
    const onAuthReject = vi.fn();
    const socket = new ReconnectingSocket({
      path: "/ws/x",
      getToken: () => "tok",
      createWebSocket: (url) => {
        const ws = new FakeWebSocket(url);
        created.push(ws);
        return ws;
      },
      onFrame: () => {},
      onStatus: () => {},
      onAuthReject,
    });
    socket.start();
    created[0].emitClose(1008);
    vi.advanceTimersByTime(10_000);
    expect(created).toHaveLength(1);
    expect(onAuthReject).toHaveBeenCalledOnce();
  });

  it("does not connect without a token", () => {
    const created: FakeWebSocket[] = [];
    const socket = new ReconnectingSocket({
      path: "/ws/x",
      getToken: () => null,
      createWebSocket: (url) => {
        const ws = new FakeWebSocket(url);
        created.push(ws);
        return ws;
      },
      onFrame: () => {},
      onStatus: () => {},
      onAuthReject: () => {},
    });
    socket.start();
    expect(created).toHaveLength(0);
  });
});

describe("consciousnessSocket", () => {
  afterEach(() => __setConsciousnessSocketFactory(undefined));

  it("feeds thought frames into the store, dedupes by id, ignores non-thoughts", () => {
    setToken("t");
    const created: FakeWebSocket[] = [];
    __setConsciousnessSocketFactory((url) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws;
    });

    const release = acquireConsciousnessStream();
    const ws = created[0];
    ws.emitOpen();
    expect(useConsciousnessStore.getState().status).toBe("open");

    ws.emitFrame({ type: "thought", id: 1, ts: "2026-05-20T09:05:00+00:00", text: "hi" });
    ws.emitFrame({ type: "thought", id: 1, ts: "2026-05-20T09:05:00+00:00", text: "hi" });
    ws.emitFrame({ type: "thought", id: 2, ts: "2026-05-20T09:06:00+00:00", text: "yo" });
    ws.emitFrame({ type: "state", id: 9 });

    const thoughts = useConsciousnessStore.getState().thoughts;
    expect(thoughts).toHaveLength(2);
    expect(thoughts.map((t) => t.id)).toEqual([1, 2]);

    release();
    expect(ws.closed).toBe(true);
  });

  it("clears the token when the stream closes 1008", () => {
    setToken("bad");
    const created: FakeWebSocket[] = [];
    __setConsciousnessSocketFactory((url) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws;
    });
    const release = acquireConsciousnessStream();
    created[0].emitClose(1008);
    expect(getToken()).toBeNull();
    release();
  });
});

describe("stateSocket", () => {
  afterEach(() => __setStateSocketFactory(undefined));

  it("feeds state frames into the store as the live snapshot", () => {
    setToken("t");
    const created: FakeWebSocket[] = [];
    __setStateSocketFactory((url) => {
      const ws = new FakeWebSocket(url);
      created.push(ws);
      return ws;
    });

    const release = acquireStateStream();
    const ws = created[0];
    ws.emitOpen();
    ws.emitFrame(loadWire("ws_state"));

    const snapshot = useStateStore.getState().snapshot;
    expect(snapshot?.tick).toBe(128);
    expect(snapshot?.drives).toHaveLength(7);

    release();
    expect(ws.closed).toBe(true);
  });
});
