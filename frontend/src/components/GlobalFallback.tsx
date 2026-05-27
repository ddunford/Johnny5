import type { ReactNode } from "react";

/**
 * Global fallback for a crash that escapes every panel boundary (e.g. the shell
 * or a provider itself throws). Offers a full reload — the headless Mind keeps
 * running regardless (FC-8), so reconnecting is always safe.
 */
export function GlobalFallback(error: Error, _reset: () => void): ReactNode {
  return (
    <div className="global-error" role="alert">
      <h1 className="global-error__title">Johnny&rsquo;s viewer crashed</h1>
      <p className="global-error__detail">{error.message}</p>
      <p className="global-error__hint">
        He&rsquo;s still running — this is only the window onto him. Reload to reconnect.
      </p>
      <button
        type="button"
        className="global-error__reload"
        onClick={() => window.location.reload()}
      >
        Reload
      </button>
    </div>
  );
}
