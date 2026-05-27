/**
 * The single source of truth for the shared access token.
 *
 * The whole `/api/v1` surface and both WebSocket streams sit behind one shared
 * token (CLAUDE.md: "single-token gate, no user system"). We keep it in
 * **sessionStorage** (NOT localStorage) so it dies with the tab and never
 * persists to disk, and we NEVER log it.
 *
 * This module is framework-agnostic and `useSyncExternalStore`-compatible: the
 * REST client (token header) and the WS clients (`?token=`) read the current
 * token from here, and on a 401 / 1008 (rejected token) they call {@link reject}
 * to clear it and bounce the UI back to the token-entry gate.
 */

const STORAGE_KEY = "johnny5.token";

export interface TokenState {
  /** The token, or null when not authenticated. */
  readonly token: string | null;
  /** True when the last token was rejected by the server (drives the gate error). */
  readonly rejected: boolean;
}

function readInitialToken(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    // sessionStorage can throw in locked-down contexts — treat as unauthenticated.
    return null;
  }
}

let state: TokenState = { token: readInitialToken(), rejected: false };
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

function setState(next: TokenState): void {
  if (next.token === state.token && next.rejected === state.rejected) {
    return;
  }
  state = next;
  emit();
}

/** Subscribe to token changes (for `useSyncExternalStore`). */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Current immutable snapshot (stable reference until a real change). */
export function getSnapshot(): TokenState {
  return state;
}

/** The current token (or null). Used by the REST/WS clients to authenticate. */
export function getToken(): string | null {
  return state.token;
}

/** Store a token the user entered, clearing any prior rejection. */
export function setToken(token: string): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // Non-persistent fallback: keep it in memory for this session only.
  }
  setState({ token, rejected: false });
}

/** Clear the token deliberately (sign out) — not a rejection. */
export function clearToken(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  setState({ token: null, rejected: false });
}

/**
 * The server rejected the token (REST 401 / WS 1008). Clear it and mark the
 * rejection so the gate can explain why the user is back at the prompt.
 */
export function reject(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  setState({ token: null, rejected: true });
}
