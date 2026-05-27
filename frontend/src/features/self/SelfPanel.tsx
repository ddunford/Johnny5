import type { ReactNode } from "react";

/**
 * Self: identity doc (name, version, values, concerns, relationships) + latest
 * metacognitive reflections. Read-only — self-edit approval is Phase 9.
 * Built out in TASK-5b.10.
 */
export function SelfPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="self-heading">
      <h2 id="self-heading" className="panel__heading">
        Self
      </h2>
      <p className="panel__placeholder">Reading his self-model&hellip;</p>
    </section>
  );
}
