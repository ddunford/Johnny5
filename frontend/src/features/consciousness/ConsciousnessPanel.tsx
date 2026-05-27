import { useEffect, useRef, type ReactNode, type UIEvent } from "react";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { useConsciousnessStatus, useThoughtsFeed } from "@/hooks/streams";
import { formatTime } from "@/lib/format";

/** Treat the view as "stuck to the bottom" when within this many px of it. */
const STICK_THRESHOLD_PX = 48;

/**
 * Johnny's live stream of consciousness from `/ws/consciousness` (Zustand-backed).
 * The store is fed continuously by the session-wide socket (backfill on connect,
 * then live + auto-reconnect), so this panel is a pure view: it renders the feed
 * and auto-scrolls to the newest thought unless you've scrolled up to read back.
 */
export function ConsciousnessPanel(): ReactNode {
  const thoughts = useThoughtsFeed();
  const status = useConsciousnessStatus();
  const containerRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  const handleScroll = (event: UIEvent<HTMLDivElement>): void => {
    const el = event.currentTarget;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = distanceFromBottom <= STICK_THRESHOLD_PX;
  };

  useEffect(() => {
    if (stickToBottom.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [thoughts.length]);

  return (
    <section className="panel" aria-labelledby="consciousness-heading">
      <div className="panel__titlebar">
        <h2 id="consciousness-heading" className="panel__heading">
          Stream of consciousness
        </h2>
        <ConnectionBadge status={status} />
      </div>

      {thoughts.length === 0 ? (
        <p className="panel__empty">
          {status === "open"
            ? "He hasn't had a thought yet — it's quiet in here."
            : "Attaching to his inner monologue…"}
        </p>
      ) : (
        <div
          className="thoughts"
          ref={containerRef}
          onScroll={handleScroll}
          role="log"
          aria-live="polite"
          aria-label="Stream of consciousness"
        >
          {thoughts.map((thought) => (
            <article key={thought.id} className="thought">
              <time className="thought__time" dateTime={thought.ts || undefined}>
                {formatTime(thought.ts)}
              </time>
              <p className="thought__text">{thought.text}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
