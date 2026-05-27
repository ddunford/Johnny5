import { useEffect, useState, type ReactNode } from "react";
import { useAudit } from "@/hooks/reads";
import { formatTime } from "@/lib/format";
import type { AuditEvent } from "@/services/types";

/** The dispatched-action event type — highlighted as the most consequential row. */
const ACTION_TYPE = "action.dispatched";

/** Render an event's payload as a compact, text-only summary (no HTML injection). */
function payloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload);
  if (entries.length === 0) {
    return "";
  }
  return entries
    .map(([key, value]) => {
      const text =
        typeof value === "string" || typeof value === "number" || typeof value === "boolean"
          ? String(value)
          : JSON.stringify(value);
      return `${key}: ${text}`;
    })
    .join(" · ");
}

function EventRow({ event }: { event: AuditEvent }): ReactNode {
  const isAction = event.type === ACTION_TYPE;
  return (
    <li className={isAction ? "audit-row audit-row--action" : "audit-row"}>
      <span className="audit-row__time">{formatTime(event.ts)}</span>
      <span className="audit-row__module">{event.module}</span>
      <span className={isAction ? "audit-row__type audit-row__type--action" : "audit-row__type"}>
        {event.type}
      </span>
      <span className="audit-row__payload">{payloadSummary(event.payload)}</span>
    </li>
  );
}

/**
 * The audit / bus-event log. Tails what the inner society broadcast — thoughts,
 * drive updates, dispatched actions — with `action.dispatched` highlighted (every
 * action Johnny takes flows through the single dispatch+audit seam, FC-5) and a
 * server-side type filter.
 */
export function AuditPanel(): ReactNode {
  const [type, setType] = useState("");
  const { data, isLoading, isError } = useAudit(type ? { type } : {});

  // Accumulate the set of event types ever seen so the filter lists them all even
  // after a narrowing filter is applied.
  const [seenTypes, setSeenTypes] = useState<string[]>([]);
  useEffect(() => {
    if (!data) {
      return;
    }
    setSeenTypes((prev) => {
      const set = new Set(prev);
      for (const event of data) {
        set.add(event.type);
      }
      const next = [...set].sort();
      return next.length === prev.length && next.every((t, i) => t === prev[i]) ? prev : next;
    });
  }, [data]);

  return (
    <section className="panel" aria-labelledby="audit-heading">
      <div className="panel__titlebar">
        <h2 id="audit-heading" className="panel__heading">
          Audit
        </h2>
        <label className="audit-filter">
          <span className="sr-only">Filter by event type</span>
          <select
            className="audit-filter__select"
            value={type}
            onChange={(event) => setType(event.target.value)}
            aria-label="Filter by event type"
          >
            <option value="">All event types</option>
            {seenTypes.map((eventType) => (
              <option key={eventType} value={eventType}>
                {eventType}
              </option>
            ))}
          </select>
        </label>
      </div>

      {isLoading ? (
        <p className="panel__placeholder">Tailing the bus…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t load the audit log.
        </p>
      ) : !data || data.length === 0 ? (
        <p className="panel__empty">
          {type ? `No "${type}" events.` : "Nothing on the bus yet."}
        </p>
      ) : (
        <ul className="audit-log">
          {data.map((event) => (
            <EventRow key={event.id} event={event} />
          ))}
        </ul>
      )}
    </section>
  );
}
