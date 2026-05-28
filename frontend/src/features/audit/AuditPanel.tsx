import { useEffect, useState, type ReactNode } from "react";
import { useAudit, useAuditActions } from "@/hooks/reads";
import { formatTime } from "@/lib/format";
import type { ActionAudit } from "@/services/auditActionsApi";
import type { AuditEvent } from "@/services/types";

/** The dispatched-action event type — highlighted as the most consequential row. */
const ACTION_TYPE = "action.dispatched";

/** Empty-state copy for the durable trail (the panel's first load before any tool runs). */
const ACTIONS_EMPTY = "No actions yet — nothing has run through the dispatch.";

/** Render a JSON-ish value map as a compact, text-only summary (no HTML injection).
 * Redacted values arrive as the literal "[REDACTED]" string and render verbatim. */
function valueSummary(payload: Record<string, unknown>): string {
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

// ── the durable action_log trail (GET /api/v1/audit/actions) ──────────────────

function ActionRow({ action }: { action: ActionAudit }): ReactNode {
  const vetoed = action.conscience_verdict === "veto";
  // result on an allowed action; args otherwise (a veto never produced a result).
  const detail = valueSummary(action.result ?? action.args);
  return (
    <li
      data-testid="audit-action-row"
      className={vetoed ? "audit-action audit-action--veto" : "audit-action"}
    >
      <span className="audit-action__time">{action.ts ? formatTime(action.ts) : ""}</span>
      <span data-testid="audit-action-tool" className="audit-action__tool">
        {action.tool}
      </span>
      <span data-testid="audit-action-verdict" className="audit-action__verdict">
        {action.conscience_verdict}
      </span>
      {vetoed && action.veto_reason ? (
        <span data-testid="audit-action-reason" className="audit-action__reason">
          {action.veto_reason}
        </span>
      ) : null}
      <span className="audit-action__detail">{detail}</span>
    </li>
  );
}

/** The durable, Core-written action trail (FC-1) — the trustworthy record that
 * survives a misbehaving Mind, distinct from the live bus feed below. */
function ActionTrail(): ReactNode {
  const [verdict, setVerdict] = useState<"" | "allow" | "veto">("");
  const { data, isLoading, isError } = useAuditActions(verdict ? { verdict } : {});

  return (
    <section className="panel" aria-labelledby="audit-actions-heading" data-testid="audit-actions">
      <div className="panel__titlebar">
        <h2 id="audit-actions-heading" className="panel__heading">
          Action trail
        </h2>
        <label className="audit-filter">
          <span className="sr-only">Filter by verdict</span>
          <select
            className="audit-filter__select"
            value={verdict}
            onChange={(event) => setVerdict(event.target.value as "" | "allow" | "veto")}
            aria-label="Filter by verdict"
          >
            <option value="">All verdicts</option>
            <option value="allow">allow</option>
            <option value="veto">veto</option>
          </select>
        </label>
      </div>

      {isLoading ? (
        <p className="panel__placeholder">Loading the action trail…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t load the action trail.
        </p>
      ) : !data || data.length === 0 ? (
        <p className="panel__empty" data-testid="audit-actions-empty">
          {verdict ? `No ${verdict} actions yet.` : ACTIONS_EMPTY}
        </p>
      ) : (
        <ul className="audit-log">
          {data.map((action, i) => (
            <ActionRow key={action.id ?? i} action={action} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ── the live bus feed (GET /api/v1/audit) ─────────────────────────────────────

function EventRow({ event }: { event: AuditEvent }): ReactNode {
  const isAction = event.type === ACTION_TYPE;
  return (
    <li className={isAction ? "audit-row audit-row--action" : "audit-row"}>
      <span className="audit-row__time">{formatTime(event.ts)}</span>
      <span className="audit-row__module">{event.module}</span>
      <span className={isAction ? "audit-row__type audit-row__type--action" : "audit-row__type"}>
        {event.type}
      </span>
      <span className="audit-row__payload">{valueSummary(event.payload)}</span>
    </li>
  );
}

function BusFeed(): ReactNode {
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
          Bus feed
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
        <p className="panel__empty">{type ? `No "${type}" events.` : "Nothing on the bus yet."}</p>
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

/**
 * The audit view: the durable Core-written **action trail** (the trustworthy
 * record, FC-1 — every effector action Johnny takes, vetted + audited, surviving
 * even a misbehaving Mind) above the live **bus feed** (the inner society's
 * broadcast stream). Each has its own server-side filter.
 */
export function AuditPanel(): ReactNode {
  return (
    <>
      <ActionTrail />
      <BusFeed />
    </>
  );
}
