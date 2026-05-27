import type { ReactNode } from "react";

/**
 * Audit: the bus/event log with a type filter; `action.dispatched` highlighted.
 * Built out in TASK-5b.9b.
 */
export function AuditPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="audit-heading">
      <h2 id="audit-heading" className="panel__heading">
        Audit
      </h2>
      <p className="panel__placeholder">Tailing the bus&hellip;</p>
    </section>
  );
}
