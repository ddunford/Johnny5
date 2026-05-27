import { useState, type ReactNode } from "react";
import { useEpisodes, useFacts } from "@/hooks/reads";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { formatDateTime, formatPercent } from "@/lib/format";
import type { Episode, Fact } from "@/services/types";

function EpisodeCard({ episode }: { episode: Episode }): ReactNode {
  return (
    <article className="mem-episode">
      <header className="mem-episode__head">
        <span className="mem-episode__kind">{episode.kind}</span>
        <span className="mem-episode__time">{formatDateTime(episode.ts)}</span>
        <span className="mem-episode__salience">salience {formatPercent(episode.salience)}</span>
        {episode.score !== null ? (
          <span className="mem-episode__score">match {formatPercent(episode.score)}</span>
        ) : null}
      </header>
      <p className="mem-episode__content">{episode.content}</p>
      <footer className="mem-episode__meta">
        {episode.actors.length > 0 ? <span>{episode.actors.join(", ")}</span> : null}
        {episode.emotion_tags.map((tag) => (
          <span key={tag} className="mem-tag">
            {tag}
          </span>
        ))}
      </footer>
    </article>
  );
}

function FactRow({ fact }: { fact: Fact }): ReactNode {
  return (
    <li className="mem-fact">
      <span className="mem-fact__triple">
        <strong>{fact.subject}</strong> {fact.predicate} <strong>{fact.object}</strong>
      </span>
      <span className="mem-fact__conf">confidence {formatPercent(fact.confidence)}</span>
      {fact.score !== null ? (
        <span className="mem-fact__score">match {formatPercent(fact.score)}</span>
      ) : null}
    </li>
  );
}

function EpisodesSection(): ReactNode {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim());
  const { data, isLoading, isError } = useEpisodes(debounced ? { q: debounced } : {});

  return (
    <section className="mem-section" aria-labelledby="episodes-heading">
      <div className="mem-section__head">
        <h3 id="episodes-heading" className="mem-section__heading">
          Episodic memory
        </h3>
        <input
          type="search"
          className="mem-search"
          placeholder="Search what he's experienced…"
          aria-label="Search episodic memory"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {isLoading ? (
        <p className="panel__placeholder">Recalling…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t load his episodes.
        </p>
      ) : !data || data.length === 0 ? (
        <p className="panel__empty">
          {debounced ? "Nothing comes to mind for that." : "No episodes yet."}
        </p>
      ) : (
        <div className="mem-episodes">
          {data.map((episode) => (
            <EpisodeCard key={episode.id} episode={episode} />
          ))}
        </div>
      )}
    </section>
  );
}

function FactsSection(): ReactNode {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim());
  const { data, isLoading, isError } = useFacts(debounced ? { q: debounced } : {});

  return (
    <section className="mem-section" aria-labelledby="facts-heading">
      <div className="mem-section__head">
        <h3 id="facts-heading" className="mem-section__heading">
          Semantic facts
        </h3>
        <input
          type="search"
          className="mem-search"
          placeholder="Search what he knows…"
          aria-label="Search semantic facts"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {isLoading ? (
        <p className="panel__placeholder">Recalling…</p>
      ) : isError ? (
        <p className="panel__empty" role="alert">
          Couldn&rsquo;t load his facts.
        </p>
      ) : !data || data.length === 0 ? (
        <p className="panel__empty">
          {debounced ? "He doesn't know anything about that yet." : "No facts yet."}
        </p>
      ) : (
        <ul className="mem-facts">
          {data.map((fact) => (
            <FactRow key={fact.id} fact={fact} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * Browse Johnny's memory: episodic experiences (recent + hybrid-recall search) and
 * the semantic facts he's consolidated (similarity search). Both via React Query.
 */
export function MemoryPanel(): ReactNode {
  return (
    <section className="panel" aria-labelledby="memory-heading">
      <h2 id="memory-heading" className="panel__heading">
        Memory
      </h2>
      <EpisodesSection />
      <FactsSection />
    </section>
  );
}
