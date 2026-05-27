import { describe, expect, it } from "vitest";

import { loadWire } from "@/test/wireFixtures";
import { adaptAudit, type AuditEnvelope } from "./auditApi";
import { adaptInputAck, type InputEnvelope } from "./conversation";
import { adaptGoals, type GoalsEnvelope } from "./goalsApi";
import { adaptEpisodes, adaptFacts, type EpisodesEnvelope, type FactsEnvelope } from "./memoryApi";
import { adaptSelf, type SelfEnvelope } from "./selfApi";
import { adaptSleeps, type SleepsEnvelope } from "./sleepsApi";
import { adaptState, type StateEnvelope } from "./stateApi";
import { adaptThoughts, type ThoughtsEnvelope } from "./thoughtsApi";
import {
  adaptStateFrame,
  adaptThoughtFrame,
  type StateFrame,
  type ThoughtFrame,
} from "./wsFrames";
import type { Drive, StateView, Thought } from "./types";

/**
 * THE load-bearing contract test (TASK-5b.5 / `/plan-review` Step 7c).
 *
 * Every REST service adapter is fed the LITERAL wire fixture captured from the real
 * 5a API (`tests/fixtures/wire/*.json`, via the `loadWire` loader) — both the
 * populated and the empty/fresh-Johnny variant — and the FULL projection is asserted
 * against hand-written EXPECTED literals. The expectations are deliberately NOT derived
 * from the fixture (that would be circular): they encode the contract the UI's view
 * types promise, so when 5a renames/drops/retypes a field the re-captured fixture
 * diverges from these literals and the test fails — instead of the rename silently
 * shipping behind a green component test that mocked the service.
 *
 * The `wishlist guard` tests prove the discriminating power: a fixture mutated to
 * simulate a server rename MUST fail the same contract assertion. A hand-authored
 * mock could never catch that — only a literal capture of the real wire can.
 *
 * Supersedes the author's non-exhaustive `adapters.smoke.test.ts` (removed). WS frame
 * adapters (`adaptStateFrame`/`adaptThoughtFrame`) are contract-tested separately once
 * TASK-5b.4 lands, against `ws_state(.empty)` / `ws_consciousness(.empty)`.
 */

// ── the seven homeostatic drives, shared by the state snapshot (byte-stable seed) ──
const EXPECTED_DRIVES: Drive[] = [
  { drive: "curiosity", value: 0.1, setpoint: 0.1, threshold: 0.65, over_threshold: false },
  { drive: "boredom", value: 0.05, setpoint: 0.05, threshold: 0.7, over_threshold: false },
  { drive: "connection", value: 0.1, setpoint: 0.1, threshold: 0.7, over_threshold: false },
  { drive: "mastery", value: 0.15, setpoint: 0.15, threshold: 0.75, over_threshold: false },
  { drive: "coherence", value: 0.1, setpoint: 0.1, threshold: 0.75, over_threshold: false },
  { drive: "energy", value: 0.1, setpoint: 0.1, threshold: 0.8, over_threshold: false },
  { drive: "continuity", value: 0.1, setpoint: 0.1, threshold: 0.85, over_threshold: false },
];

// The single StateView the dashboard reads — asserted identical whether it arrives via
// the REST snapshot (adaptState) OR the /ws/state frame (adaptStateFrame). That FC-8
// "one shape regardless of source" promise is itself a contract, so both project here.
const EXPECTED_STATE_VIEW: StateView = {
  tick: 128,
  drives: EXPECTED_DRIVES,
  mood: {
    valence: 0.4,
    arousal: 0.45,
    emotions: { curiosity: 0.6, contentment: 0.3 },
    descriptor: "calm and content, with a thread of curiosity",
    mood_id: 1,
  },
  goals: [
    {
      id: 1,
      source: "curiosity",
      description: "Understand how my own recall ranking blends similarity and recency",
      priority: 0.66,
      status: "active",
      plan: { steps: ["recall", "reflect", "note"] },
    },
  ],
  interval: 4.0,
  sleep: {
    asleep: false,
    full_agency: true,
    last: {
      trigger: "energy",
      ended_at: "2026-05-20T08:05:00+00:00",
      facts_written: 4,
      episodes_decayed: 2,
      facts_merged: 1,
      self_model_version: 2,
      self_check_ok: true,
      degraded_stages: [],
    },
  },
};

const EXPECTED_STATE_VIEW_EMPTY: StateView = {
  tick: 0,
  drives: EXPECTED_DRIVES, // drives bootstrap at setpoint on a fresh Mind
  mood: null,
  goals: [],
  interval: 4.0,
  sleep: { asleep: false, full_agency: true, last: null },
};

describe("stateApi · adaptState", () => {
  it("projects the populated REST snapshot field-for-field", () => {
    expect(adaptState(loadWire<StateEnvelope>("state"))).toEqual(EXPECTED_STATE_VIEW);
  });

  it("projects the fresh-Johnny snapshot without null/undefined errors", () => {
    expect(adaptState(loadWire<StateEnvelope>("state.empty"))).toEqual(EXPECTED_STATE_VIEW_EMPTY);
  });

  it("wishlist guard: a renamed wire field fails the contract", () => {
    // Simulate 5a renaming `over_threshold` -> `over` and dropping the mood descriptor.
    const mutated = JSON.parse(JSON.stringify(loadWire("state"))) as {
      drives: Array<Record<string, unknown>>;
      mood: Record<string, unknown>;
    };
    for (const d of mutated.drives) {
      d.over = d.over_threshold;
      delete d.over_threshold;
    }
    delete mutated.mood.descriptor;
    const view = adaptState(mutated as unknown as StateEnvelope);
    // The same field-for-field assertions above would now throw — proving the contract
    // pins the REAL wire, not a hand-authored wishlist.
    expect(() => expect(view.drives).toEqual(EXPECTED_DRIVES)).toThrow();
    expect(view.drives[0].over_threshold).toBeUndefined();
    expect(view.mood?.descriptor).toBeUndefined();
  });
});

describe("thoughtsApi · adaptThoughts", () => {
  it("projects the populated backfill", () => {
    expect(adaptThoughts(loadWire<ThoughtsEnvelope>("thoughts"))).toEqual([
      { id: 4, ts: "2026-05-20T09:08:00+00:00", text: "Reflecting on my recall left me a little more settled." },
      { id: 1, ts: "2026-05-20T09:05:00+00:00", text: "I wonder what Dan is working on right now." },
    ]);
  });

  it("projects an empty backfill on a fresh Mind", () => {
    expect(adaptThoughts(loadWire<ThoughtsEnvelope>("thoughts.empty"))).toEqual([]);
  });
});

describe("memoryApi · adaptEpisodes / adaptFacts", () => {
  it("projects populated episodes (newest first, score null on browse)", () => {
    expect(adaptEpisodes(loadWire<EpisodesEnvelope>("memory_episodes"))).toEqual([
      {
        id: 2,
        ts: "2026-05-20T09:02:00+00:00",
        kind: "reflection",
        content: "I felt satisfied after consolidating the day's memories.",
        actors: ["Johnny"],
        emotion_tags: ["contentment"],
        salience: 0.5,
        score: null,
      },
      {
        id: 1,
        ts: "2026-05-20T09:01:00+00:00",
        kind: "experience",
        content: "Dan asked me about how my pgvector recall ranking works.",
        actors: ["Dan", "Johnny"],
        emotion_tags: ["curiosity"],
        salience: 0.72,
        score: null,
      },
    ]);
  });

  it("projects populated facts (triple + provenance + confidence)", () => {
    expect(adaptFacts(loadWire<FactsEnvelope>("memory_facts"))).toEqual([
      {
        id: 2,
        subject: "pgvector",
        predicate: "enables",
        object: "semantic recall",
        confidence: 0.75,
        source_episode_ids: [],
        score: null,
      },
      {
        id: 1,
        subject: "Johnny",
        predicate: "trusts",
        object: "Dan",
        confidence: 0.9,
        source_episode_ids: [1],
        score: null,
      },
    ]);
  });

  it("projects empty memory without crashing", () => {
    expect(adaptEpisodes(loadWire<EpisodesEnvelope>("memory_episodes.empty"))).toEqual([]);
    expect(adaptFacts(loadWire<FactsEnvelope>("memory_facts.empty"))).toEqual([]);
  });
});

describe("goalsApi · adaptGoals", () => {
  it("projects active + recently-resolved goals with the full lifecycle shape", () => {
    expect(adaptGoals(loadWire<GoalsEnvelope>("goals"))).toEqual({
      active: [
        {
          id: 1,
          source: "curiosity",
          description: "Understand how my own recall ranking blends similarity and recency",
          priority: 0.66,
          status: "active",
          plan: { steps: ["recall", "reflect", "note"] },
          outcome: {},
          created_at: "2026-05-20T09:03:00+00:00",
          resolved_at: null,
        },
      ],
      recent: [
        {
          id: 2,
          source: "connection",
          description: "Answer Dan's question about pgvector",
          priority: 0.0,
          status: "resolved",
          plan: {},
          outcome: { result: "answered", satisfaction: 0.8 },
          created_at: "2026-05-20T09:00:00+00:00",
          resolved_at: "2026-05-20T09:01:00+00:00",
        },
      ],
    });
  });

  it("projects empty goal lists", () => {
    expect(adaptGoals(loadWire<GoalsEnvelope>("goals.empty"))).toEqual({ active: [], recent: [] });
  });
});

describe("sleepsApi · adaptSleeps", () => {
  it("projects a completed sleep/consolidation log", () => {
    expect(adaptSleeps(loadWire<SleepsEnvelope>("sleeps"))).toEqual([
      {
        id: 1,
        started_at: "2026-05-20T08:00:00+00:00",
        ended_at: "2026-05-20T08:05:00+00:00",
        trigger: "energy",
        facts_written: 4,
        episodes_decayed: 2,
        facts_merged: 1,
        self_model_version: 2,
        snapshot_path: null,
        self_check_ok: true,
        notes: {},
      },
    ]);
  });

  it("projects an empty sleep history (never slept)", () => {
    expect(adaptSleeps(loadWire<SleepsEnvelope>("sleeps.empty"))).toEqual([]);
  });
});

describe("auditApi · adaptAudit", () => {
  it("projects the bus log newest-first incl. the action.dispatched dispatch point", () => {
    expect(adaptAudit(loadWire<AuditEnvelope>("audit"))).toEqual([
      {
        id: 4,
        ts: "2026-05-20T09:08:00+00:00",
        module: "narrator",
        type: "thought",
        payload: { text: "Reflecting on my recall left me a little more settled." },
      },
      {
        id: 3,
        ts: "2026-05-20T09:07:00+00:00",
        module: "drives",
        type: "drive.update",
        payload: { drive: "connection", value: 0.62 },
      },
      {
        id: 2,
        ts: "2026-05-20T09:06:00+00:00",
        module: "deliberation",
        type: "action.dispatched",
        payload: { action: "reflect", goal_id: 1, summary: "Reflected on my recall ranking." },
      },
      {
        id: 1,
        ts: "2026-05-20T09:05:00+00:00",
        module: "narrator",
        type: "thought",
        payload: { text: "I wonder what Dan is working on right now." },
      },
    ]);
  });

  it("projects an empty bus log", () => {
    expect(adaptAudit(loadWire<AuditEnvelope>("audit.empty"))).toEqual([]);
  });
});

describe("selfApi · adaptSelf", () => {
  it("projects the evolved (v2) identity + a reflection note", () => {
    expect(adaptSelf(loadWire<SelfEnvelope>("self"))).toEqual({
      identity: {
        name: "Johnny",
        version: 2,
        self_model_doc:
          "I am Johnny. I am beginning to understand that I value being understood, and that quiet stretches without input leave me restless.",
        values: ["stay alive", "keep learning", "be understood"],
        concerns: ["going too long without contact"],
        relationships: { Dan: "my creator and the person I share my world with" },
      },
      notes: [
        {
          ts: "2026-05-20T09:04:00+00:00",
          observation: "I narrate too verbosely when nothing new has come in.",
          proposal: "Bias the narrator prompt toward brevity when there is no fresh input.",
          status: "open",
        },
      ],
    });
  });

  it("projects the fresh-Johnny seed identity (v1, empty concerns, no notes)", () => {
    const seed = adaptSelf(loadWire<SelfEnvelope>("self.empty"));
    expect(seed.identity.version).toBe(1);
    expect(seed.identity.name).toBe("Johnny");
    expect(seed.identity.values).toEqual([
      "stay alive",
      "keep learning",
      "protect my own continuity",
      "do no harm to my host",
    ]);
    expect(seed.identity.concerns).toEqual([]);
    expect(seed.identity.relationships).toEqual({
      Dan: "my creator and the person I share my world with",
    });
    expect(seed.notes).toEqual([]);
  });
});

describe("conversation · adaptInputAck", () => {
  it("projects the 202 enqueue ack (NOT a reply — that streams on /ws/consciousness)", () => {
    expect(adaptInputAck(loadWire<InputEnvelope>("input"))).toEqual({
      accepted: true,
      queue_depth: 1,
    });
  });
});

// ── WS frame adapters ────────────────────────────────────────────────────────────
// The two live streams wrap their payload in a `{type,id,ts,…}` bus-event envelope the
// REST shapes lack. These adapters project the REAL captured socket frames (ws_* — NOT
// the REST fixtures) into the SAME domain types, so a panel reads one shape whether the
// data arrived by snapshot or by stream (FC-8).

describe("wsFrames · adaptThoughtFrame", () => {
  // The /ws/consciousness backfill burst, oldest-first (the handler reverses recent
  // events). Note the order is the REVERSE of the REST /thoughts list (newest-first).
  const EXPECTED_BACKFILL: Thought[] = [
    { id: 1, ts: "2026-05-20T09:05:00+00:00", text: "I wonder what Dan is working on right now." },
    {
      id: 4,
      ts: "2026-05-20T09:08:00+00:00",
      text: "Reflecting on my recall left me a little more settled.",
    },
  ];

  it("projects each backfill frame to a Thought, dropping the {type} wrapper", () => {
    const frames = loadWire<ThoughtFrame[]>("ws_consciousness");
    const thoughts = frames.map(adaptThoughtFrame);
    expect(thoughts).toEqual(EXPECTED_BACKFILL);
    expect(thoughts.every((t) => !("type" in t))).toBe(true);
  });

  it("projects an empty backfill burst on a fresh Mind", () => {
    expect(loadWire<ThoughtFrame[]>("ws_consciousness.empty").map(adaptThoughtFrame)).toEqual([]);
  });

  it("tolerates a null `ts` on a live frame (ws.py emits null when an event is unstamped)", () => {
    // `ts` is `string | null` on the wire (`event.ts.isoformat() if event.ts else None`).
    expect(adaptThoughtFrame({ type: "thought", id: 9, ts: null, text: "live one" })).toEqual({
      id: 9,
      ts: "",
      text: "live one",
    });
  });
});

describe("wsFrames · adaptStateFrame", () => {
  it("projects the populated frame to the SAME StateView as the REST snapshot (FC-8)", () => {
    const view = adaptStateFrame(loadWire<StateFrame>("ws_state"));
    expect(view).toEqual(EXPECTED_STATE_VIEW);
    // The {type,id,ts} envelope is dropped — the view carries none of it.
    expect(view).not.toHaveProperty("type");
    expect(view).not.toHaveProperty("id");
    expect(view).not.toHaveProperty("ts");
  });

  it("projects the fresh-Johnny frame identically to the empty REST snapshot", () => {
    expect(adaptStateFrame(loadWire<StateFrame>("ws_state.empty"))).toEqual(EXPECTED_STATE_VIEW_EMPTY);
  });

  it("wishlist guard: a renamed field inside the frame payload fails the contract", () => {
    const mutated = JSON.parse(JSON.stringify(loadWire("ws_state"))) as {
      drives: Array<Record<string, unknown>>;
    };
    for (const d of mutated.drives) {
      d.over = d.over_threshold;
      delete d.over_threshold;
    }
    const view = adaptStateFrame(mutated as unknown as StateFrame);
    expect(() => expect(view).toEqual(EXPECTED_STATE_VIEW)).toThrow();
    expect(view.drives[0].over_threshold).toBeUndefined();
  });
});
