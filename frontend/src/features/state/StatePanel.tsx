import type { ReactNode } from "react";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { useLiveState, useStateStreamStatus } from "@/hooks/streams";
import { useStateSnapshot } from "@/hooks/reads";
import { formatDateTime, formatPercent } from "@/lib/format";
import type { Drive, Mood, SleepStatus, StateGoal, StateView } from "@/services/types";

function DriveBar({ drive }: { drive: Drive }): ReactNode {
  const valuePct = `${Math.min(100, Math.max(0, drive.value * 100))}%`;
  const thresholdPct = `${Math.min(100, Math.max(0, drive.threshold * 100))}%`;
  return (
    <div className="drive">
      <div className="drive__head">
        <span className="drive__name">{drive.drive}</span>
        <span className={drive.over_threshold ? "drive__value drive__value--over" : "drive__value"}>
          {formatPercent(drive.value)}
        </span>
      </div>
      <div className="drive__track">
        <div
          className="drive__fill"
          data-over={drive.over_threshold ? "true" : "false"}
          style={{ width: valuePct }}
        />
        <div
          className="drive__threshold"
          style={{ left: thresholdPct }}
          title={`threshold ${formatPercent(drive.threshold)}`}
          aria-hidden="true"
        />
      </div>
    </div>
  );
}

function MoodBlock({ mood }: { mood: Mood | null }): ReactNode {
  if (!mood) {
    return <p className="panel__empty">No mood yet — he hasn&rsquo;t appraised anything.</p>;
  }
  const emotions = Object.entries(mood.emotions);
  return (
    <div className="mood">
      <p className="mood__descriptor">{mood.descriptor}</p>
      <div className="mood__metrics">
        <span>valence {mood.valence.toFixed(2)}</span>
        <span>arousal {mood.arousal.toFixed(2)}</span>
      </div>
      {emotions.length > 0 ? (
        <ul className="mood__emotions">
          {emotions.map(([name, weight]) => (
            <li key={name} className="mood__emotion">
              {name} {formatPercent(weight)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ActiveGoal({ goals }: { goals: StateGoal[] }): ReactNode {
  const active = goals.find((goal) => goal.status === "active") ?? goals[0];
  if (!active) {
    return <p className="panel__empty">No active goal right now.</p>;
  }
  return (
    <div className="goal">
      <p className="goal__desc">{active.description}</p>
      <p className="goal__meta">
        <span>from {active.source}</span>
        <span>priority {formatPercent(active.priority)}</span>
        {active.plan.steps && active.plan.steps.length > 0 ? (
          <span>plan: {active.plan.steps.join(" → ")}</span>
        ) : null}
      </p>
    </div>
  );
}

function SleepBlock({ sleep }: { sleep: SleepStatus }): ReactNode {
  const last = sleep.last;
  return (
    <div className="sleep">
      <p className="sleep__status">
        <span className={sleep.asleep ? "badge badge--asleep" : "badge badge--awake"}>
          {sleep.asleep ? "Asleep" : "Awake"}
        </span>
        {!sleep.full_agency ? (
          <span className="badge badge--degraded" role="alert">
            ⚠ DEGRADED
          </span>
        ) : null}
      </p>
      {last ? (
        <p className="sleep__last">
          Last slept {formatDateTime(last.ended_at)} ({last.trigger}) · {last.facts_written} facts
          written, {last.episodes_decayed} decayed, {last.facts_merged} merged
          {last.degraded_stages.length > 0
            ? ` · degraded: ${last.degraded_stages.join(", ")}`
            : ""}
        </p>
      ) : (
        <p className="sleep__last sleep__last--never">He has never slept yet.</p>
      )}
    </div>
  );
}

/** The self-model version recorded at his last consolidation; seed v1 if never slept. */
function selfModelVersionLabel(sleep: SleepStatus): string {
  const version = sleep.last?.self_model_version ?? 1;
  return sleep.last ? `v${version}` : "v1 (seed)";
}

function Dashboard({ snapshot }: { snapshot: StateView }): ReactNode {
  return (
    <>
      <div className="state-summary">
        <div className="state-stat">
          <span className="state-stat__label">Heartbeat</span>
          <span className="state-stat__value">{snapshot.interval.toFixed(1)}s</span>
        </div>
        <div className="state-stat">
          <span className="state-stat__label">Tick</span>
          <span className="state-stat__value">{snapshot.tick}</span>
        </div>
        <div className="state-stat">
          <span className="state-stat__label">Self-model</span>
          <span className="state-stat__value">{selfModelVersionLabel(snapshot.sleep)}</span>
        </div>
      </div>

      <SleepBlock sleep={snapshot.sleep} />

      <div className="state-grid">
        <section className="state-card" aria-labelledby="drives-heading">
          <h3 id="drives-heading" className="state-card__heading">
            Drives
          </h3>
          <div className="drives">
            {snapshot.drives.map((drive) => (
              <DriveBar key={drive.drive} drive={drive} />
            ))}
          </div>
        </section>

        <section className="state-card" aria-labelledby="mood-heading">
          <h3 id="mood-heading" className="state-card__heading">
            Mood
          </h3>
          <MoodBlock mood={snapshot.mood} />
        </section>

        <section className="state-card" aria-labelledby="goal-heading">
          <h3 id="goal-heading" className="state-card__heading">
            Active goal
          </h3>
          <ActiveGoal goals={snapshot.goals} />
        </section>
      </div>
    </>
  );
}

/**
 * The "he's alive" dashboard. Paints first from the REST snapshot
 * (`GET /api/v1/state`) and then the `/ws/state` stream takes over (FC-8) — drives
 * climbing, mood shifting, a goal appearing, falling asleep/waking, the DEGRADED
 * (full_agency=false) flag — all with no input.
 */
export function StatePanel(): ReactNode {
  const live = useLiveState();
  const status = useStateStreamStatus();
  const { data: rest, isLoading, isError, error } = useStateSnapshot();
  const snapshot = live ?? rest ?? null;

  return (
    <section className="panel" aria-labelledby="state-heading">
      <div className="panel__titlebar">
        <h2 id="state-heading" className="panel__heading">
          State
        </h2>
        <ConnectionBadge status={status} />
      </div>

      {snapshot ? (
        <Dashboard snapshot={snapshot} />
      ) : isLoading ? (
        <p className="panel__placeholder">Reading his vitals…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t read his state{error instanceof Error ? `: ${error.message}` : ""}. The
          live stream may still reconnect.
        </p>
      ) : (
        <p className="panel__placeholder">Waiting for his first heartbeat…</p>
      )}
    </section>
  );
}
