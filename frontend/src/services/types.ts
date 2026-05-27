/**
 * Shared domain (view) types — the shapes the UI consumes after each service
 * adapter projects its `ServerEnvelope`. These deliberately mirror the captured
 * wire fixtures (`tests/fixtures/wire/*.json`) field-for-field; the per-service
 * `ServerEnvelope` types (which model the raw 5a response wrappers) live next to
 * each adapter and project INTO these.
 */

/** One homeostatic drive (state snapshot + `/ws/state` frame). */
export interface Drive {
  drive: string;
  value: number;
  setpoint: number;
  threshold: number;
  over_threshold: boolean;
}

/** Current mood. `null` on a fresh Mind that hasn't appraised anything yet. */
export interface Mood {
  valence: number;
  arousal: number;
  emotions: Record<string, number>;
  descriptor: string;
  mood_id: number;
}

/** The slim goal carried in the state snapshot / `/ws/state` frame. */
export interface StateGoal {
  id: number;
  source: string;
  description: string;
  priority: number;
  status: string;
  plan: GoalPlan;
}

/** A goal's plan — `{steps:[...]}` when planned, `{}` when not. */
export interface GoalPlan {
  steps?: string[];
}

/** Summary of the most recent sleep (consolidation) — `null` if he's never slept. */
export interface SleepLast {
  trigger: string;
  ended_at: string;
  facts_written: number;
  episodes_decayed: number;
  facts_merged: number;
  self_model_version: number;
  self_check_ok: boolean;
  degraded_stages: string[];
}

/** Awake/asleep + the Core full-agency gate + last-sleep summary. */
export interface SleepStatus {
  asleep: boolean;
  full_agency: boolean;
  last: SleepLast | null;
}

/**
 * The consolidated live state — identical whether sourced from the REST snapshot
 * (`GET /api/v1/state`, initial paint) or the `/ws/state` frame (live, FC-8), so
 * the dashboard reads one shape regardless of source.
 */
export interface StateView {
  tick: number;
  drives: Drive[];
  mood: Mood | null;
  goals: StateGoal[];
  interval: number;
  sleep: SleepStatus;
}

/** A single first-person thought (REST backfill + `/ws/consciousness` frame). */
export interface Thought {
  id: number;
  ts: string;
  text: string;
}

/** An episodic memory. `score` is non-null only on a hybrid-recall search result. */
export interface Episode {
  id: number;
  ts: string;
  kind: string;
  content: string;
  actors: string[];
  emotion_tags: string[];
  salience: number;
  score: number | null;
}

/** A semantic fact (subject–predicate–object). */
export interface Fact {
  id: number;
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
  source_episode_ids: number[];
  score: number | null;
}

/** A full goal record (`/api/v1/goals`) — richer than the state-frame {@link StateGoal}. */
export interface Goal {
  id: number;
  source: string;
  description: string;
  priority: number;
  status: string;
  plan: GoalPlan;
  outcome: Record<string, unknown>;
  created_at: string;
  resolved_at: string | null;
}

/** Active + recently-resolved goals. */
export interface GoalsView {
  active: Goal[];
  recent: Goal[];
}

/** A completed sleep/consolidation log row (`/api/v1/sleeps`). */
export interface SleepLog {
  id: number;
  started_at: string;
  ended_at: string;
  trigger: string;
  facts_written: number;
  episodes_decayed: number;
  facts_merged: number;
  self_model_version: number;
  snapshot_path: string | null;
  self_check_ok: boolean;
  notes: Record<string, unknown>;
}

/** Johnny's identity doc (`/api/v1/self`). */
export interface Identity {
  name: string;
  version: number;
  self_model_doc: string;
  values: string[];
  concerns: string[];
  relationships: Record<string, string>;
}

/** A metacognitive reflection / proposal. */
export interface SelfNote {
  ts: string;
  observation: string;
  proposal: string;
  status: string;
}

/** Identity + latest reflections. */
export interface SelfView {
  identity: Identity;
  notes: SelfNote[];
}

/** A bus/event-log entry (`/api/v1/audit`). */
export interface AuditEvent {
  id: number;
  ts: string;
  module: string;
  type: string;
  payload: Record<string, unknown>;
}

/** The 202 acknowledgement of a sent message (`POST /api/v1/input`). */
export interface InputAck {
  accepted: boolean;
  queue_depth: number;
}
