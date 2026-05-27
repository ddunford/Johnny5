import type { ReactNode } from "react";
import type { ConnectionStatus } from "@/services/connectionStatus";

const LABELS: Record<ConnectionStatus, string> = {
  connecting: "connecting",
  open: "live",
  reconnecting: "reconnecting…",
  closed: "offline",
};

/** A small status pill for a live WS stream's health. */
export function ConnectionBadge({ status }: { status: ConnectionStatus }): ReactNode {
  return (
    <span className={`conn conn--${status}`} role="status" aria-label={`Stream ${LABELS[status]}`}>
      <span className="conn__dot" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
