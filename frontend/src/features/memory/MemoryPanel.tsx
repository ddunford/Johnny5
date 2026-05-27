import type { ReactNode } from "react";

/**
 * Memory browser: episodic (recent + search) and semantic facts (search).
 * Built out in TASK-5b.9a.
 */
export function MemoryPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="memory-heading">
      <h2 id="memory-heading" className="panel__heading">
        Memory
      </h2>
      <p className="panel__placeholder">Opening his memory&hellip;</p>
    </section>
  );
}
