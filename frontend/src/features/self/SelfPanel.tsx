import type { ReactNode } from "react";
import { useSelf } from "@/hooks/reads";
import { formatDateTime } from "@/lib/format";
import type { Identity, SelfNote } from "@/services/types";

function Chips({ items, empty }: { items: string[]; empty: string }): ReactNode {
  if (items.length === 0) {
    return <p className="self-empty">{empty}</p>;
  }
  return (
    <ul className="self-chips">
      {items.map((item) => (
        <li key={item} className="self-chip">
          {item}
        </li>
      ))}
    </ul>
  );
}

function IdentityBlock({ identity }: { identity: Identity }): ReactNode {
  const relationships = Object.entries(identity.relationships);
  return (
    <div className="self-identity">
      <div className="self-identity__head">
        <span className="self-identity__name">{identity.name}</span>
        <span className="badge badge--awake">self-model v{identity.version}</span>
      </div>
      <p className="self-doc">{identity.self_model_doc}</p>

      <h3 className="self-subheading">Values</h3>
      <Chips items={identity.values} empty="No values recorded yet." />

      <h3 className="self-subheading">Concerns</h3>
      <Chips items={identity.concerns} empty="Nothing weighing on him right now." />

      <h3 className="self-subheading">Relationships</h3>
      {relationships.length === 0 ? (
        <p className="self-empty">No relationships recorded yet.</p>
      ) : (
        <dl className="self-relationships">
          {relationships.map(([who, how]) => (
            <div key={who} className="self-relationship">
              <dt>{who}</dt>
              <dd>{how}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function ReflectionRow({ note }: { note: SelfNote }): ReactNode {
  return (
    <li className="self-note">
      <div className="self-note__head">
        <span className="self-note__time">{formatDateTime(note.ts)}</span>
        <span className="self-note__status">{note.status}</span>
      </div>
      <p className="self-note__observation">{note.observation}</p>
      {note.proposal ? (
        <p className="self-note__proposal">
          <span className="self-note__proposal-label">proposes</span> {note.proposal}
        </p>
      ) : null}
    </li>
  );
}

/**
 * Johnny's self-model: his identity doc (name, version, the prose he's written
 * about himself, values, concerns, relationships) and his latest metacognitive
 * reflections. **Read-only** — acting on a self-edit proposal (approve/reject of a
 * pending code change) is the Phase-9 self-modification gate, surfaced below as a
 * labelled placeholder, NOT stubbed as a fake flow.
 */
export function SelfPanel(): ReactNode {
  const { data, isLoading, isError, error } = useSelf();

  return (
    <section className="panel" aria-labelledby="self-heading">
      <h2 id="self-heading" className="panel__heading">
        Self
      </h2>

      {isLoading ? (
        <p className="panel__placeholder">Reading his self-model…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t read his self-model{error instanceof Error ? `: ${error.message}` : ""}.
        </p>
      ) : !data ? (
        <p className="panel__placeholder">Reading his self-model…</p>
      ) : (
        <>
          <IdentityBlock identity={data.identity} />

          <section className="self-section" aria-labelledby="reflections-heading">
            <h3 id="reflections-heading" className="self-subheading">
              Reflections
            </h3>
            {data.notes.length === 0 ? (
              <p className="self-empty">He hasn&rsquo;t reflected on himself yet.</p>
            ) : (
              <ul className="self-notes">
                {data.notes.map((note, index) => (
                  <ReflectionRow key={`${note.ts}-${index}`} note={note} />
                ))}
              </ul>
            )}
          </section>

          <p className="self-phase9" role="note">
            Reviewing and approving his self-edits (the propose → sandbox → approve gate)
            arrives in Phase&nbsp;9. This view is read-only.
          </p>
        </>
      )}
    </section>
  );
}
