import type { ReactNode } from "react";

/**
 * Live first-person thought feed from `/ws/consciousness`.
 * Built out in TASK-5b.7 (feed + backfill + auto-scroll + reconnect).
 */
export function ConsciousnessPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="consciousness-heading">
      <h2 id="consciousness-heading" className="panel__heading">
        Stream of consciousness
      </h2>
      <p className="panel__placeholder">Attaching to his inner monologue&hellip;</p>
    </section>
  );
}
