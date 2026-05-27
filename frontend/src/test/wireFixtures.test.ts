import { describe, expect, it } from "vitest";

import { loadWire, loadWirePair, tryLoadWire } from "./wireFixtures";

/**
 * Self-test for the wire-fixture loader (TASK-5b.5 scaffolding). This does NOT test any
 * service adapter (those land with 5b.3b/5b.4) — it proves the loader resolves the
 * repo-root `tests/fixtures/wire/` files and that every fixture the contract tests will
 * consume is present with its captured top-level shape. If a capture is renamed/removed
 * or the cross-package path breaks, this fails loudly instead of a contract test
 * silently skipping its input.
 */

// Every wire surface a 5b service adapter pins against. `hasEmpty` = an `.empty.json`
// fresh-Johnny variant exists (the first-paint null trap). The two WS frames were
// captured by the 5b WS-frame addition; the rest are the 5a REST envelopes.
const REST_SURFACES = [
  "state",
  "thoughts",
  "audit",
  "memory_episodes",
  "memory_facts",
  "goals",
  "sleeps",
  "self",
] as const;

describe("wire fixture loader", () => {
  it("loads every REST surface with both populated and empty-state captures", () => {
    for (const name of REST_SURFACES) {
      const { populated, empty } = loadWirePair<Record<string, unknown>>(name);
      expect(populated, `${name}.json`).toBeTypeOf("object");
      expect(empty, `${name}.empty.json`).toBeTypeOf("object");
    }
  });

  it("loads the POST /input ack fixture", () => {
    expect(loadWire("input")).toEqual({ accepted: true, queue_depth: 1 });
  });

  it("pins the captured REST top-level shapes the adapters key off", () => {
    expect(loadWire<{ thoughts: unknown[] }>("thoughts").thoughts).toBeInstanceOf(Array);
    expect(loadWire<{ events: unknown[] }>("audit").events).toBeInstanceOf(Array);
    expect(loadWire<{ episodes: unknown[] }>("memory_episodes").episodes).toBeInstanceOf(Array);
    expect(loadWire<{ facts: unknown[] }>("memory_facts").facts).toBeInstanceOf(Array);
    expect(loadWire<{ active: unknown[]; recent: unknown[] }>("goals")).toMatchObject({
      active: expect.any(Array),
      recent: expect.any(Array),
    });
    expect(loadWire<{ sleeps: unknown[] }>("sleeps").sleeps).toBeInstanceOf(Array);
    expect(loadWire<{ identity: object; notes: unknown[] }>("self")).toMatchObject({
      identity: expect.any(Object),
      notes: expect.any(Array),
    });
    // State carries the drive array + the sleep block the dashboard reads.
    const state = loadWire<{ drives: unknown[]; sleep: object }>("state");
    expect(state.drives).toBeInstanceOf(Array);
    expect(state.sleep).toBeTypeOf("object");
  });

  it("pins the empty-state null traps (the bug class Step 7c exists to catch)", () => {
    const state = loadWire<{ mood: unknown; goals: unknown[]; sleep: { last: unknown } }>(
      "state.empty",
    );
    expect(state.mood).toBeNull();
    expect(state.goals).toEqual([]);
    expect(state.sleep.last).toBeNull();
    expect(loadWire<{ thoughts: unknown[] }>("thoughts.empty").thoughts).toEqual([]);
    expect(loadWire<{ events: unknown[] }>("audit.empty").events).toEqual([]);
    // Fresh self is the seeded v1 anchor (NOT null) with no notes yet.
    expect(loadWire<{ identity: { version: number }; notes: unknown[] }>("self.empty")).toMatchObject(
      { identity: { version: 1 }, notes: [] },
    );
  });

  it("loads the WS frame captures — the {type,id,ts,…} wrapper the REST shapes lack", () => {
    // /ws/consciousness backfill burst: an array of thought frames (empty on a fresh Mind).
    const wsThoughts = loadWire<Array<{ type: string; text: string }>>("ws_consciousness");
    expect(wsThoughts).toBeInstanceOf(Array);
    expect(wsThoughts.length).toBeGreaterThan(0);
    for (const frame of wsThoughts) {
      expect(Object.keys(frame).sort()).toEqual(["id", "text", "ts", "type"]);
      expect(frame.type).toBe("thought");
    }
    expect(loadWire("ws_consciousness.empty")).toEqual([]);

    // /ws/state: a single snapshot frame = the {type,id,ts} wrapper + the state payload.
    const wsState = loadWire<Record<string, unknown>>("ws_state");
    expect(wsState.type).toBe("state");
    for (const key of ["id", "ts", "tick", "drives", "mood", "goals", "interval", "sleep"]) {
      expect(wsState, `ws_state missing ${key}`).toHaveProperty(key);
    }
    const wsStateEmpty = loadWire<{ type: string; mood: unknown; sleep: { last: unknown } }>(
      "ws_state.empty",
    );
    expect(wsStateEmpty.type).toBe("state");
    expect(wsStateEmpty.mood).toBeNull();
    expect(wsStateEmpty.sleep.last).toBeNull();
  });

  it("returns undefined for an absent fixture rather than throwing", () => {
    expect(tryLoadWire("does_not_exist")).toBeUndefined();
  });
});
