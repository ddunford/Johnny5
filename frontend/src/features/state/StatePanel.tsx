import type { ReactNode } from "react";

/**
 * State dashboard: drive bars, mood, active goal, heartbeat interval, awake/asleep
 * + DEGRADED flag, self-model version, last-sleep summary — from `/ws/state`.
 * Built out in TASK-5b.8.
 */
export function StatePanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="state-heading">
      <h2 id="state-heading" className="panel__heading">
        State
      </h2>
      <p className="panel__placeholder">Reading his vitals&hellip;</p>
    </section>
  );
}
