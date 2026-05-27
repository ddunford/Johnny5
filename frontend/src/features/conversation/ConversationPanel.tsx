import type { ReactNode } from "react";

/**
 * Talk to Johnny: a message becomes a percept he thinks about; his reply emerges
 * as a thought on the consciousness stream (no reply endpoint by design).
 * Built out in TASK-5b.6.
 */
export function ConversationPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="conversation-heading">
      <h2 id="conversation-heading" className="panel__heading">
        Conversation
      </h2>
      <p className="panel__placeholder">Opening a line to him&hellip;</p>
    </section>
  );
}
