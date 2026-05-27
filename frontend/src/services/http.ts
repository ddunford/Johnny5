/**
 * The REST client core for the `/api/v1` surface (Phase 5a).
 *
 * Every authenticated read/write goes through {@link request}, which:
 *   1. prefixes the versioned API base (same-origin: Vite proxies in dev, Traefik
 *      routes `/api` → the api service in prod),
 *   2. injects the shared token as `Authorization: Bearer <token>` from
 *      {@link tokenStore} (never logged, never put in the URL),
 *   3. on **401** clears the token via {@link tokenStore.reject} so the UI bounces
 *      back to the entry gate, and
 *   4. parses JSON, raising {@link ApiError} (carrying `status`) on any non-2xx so
 *      React Query's retry logic can skip auth failures.
 *
 * ── ServerEnvelope convention (house rule + /plan-review Step 7c) ──────────────
 * Each `services/*.ts` adapter that wraps an endpoint MUST:
 *   • declare a `ServerEnvelope` (or `*Envelope`) type that models 5a's ACTUAL
 *     response — copied from `tests/fixtures/wire/<endpoint>.json`, with the
 *     endpoint cited in a comment;
 *   • expose a pure `adapt…(envelope): <View>` projection;
 *   • be covered by a contract test (TASK-5b.5) fed the LITERAL wire fixture
 *     (populated + `.empty.json`).
 * A hand-authored response interface is a CLAIM, not a proof — only the fixture-fed
 * contract test proves the shape matches the server.
 */

import { getToken, reject } from "@/auth/tokenStore";

/** Versioned API base. Same-origin in both dev (Vite proxy) and prod (Traefik). */
export const API_BASE = "/api/v1";

/** An HTTP error carrying the status code (used by the React Query retry guard). */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export type QueryParams = Record<string, string | number | boolean | null | undefined>;

interface RequestOptions {
  method?: "GET" | "POST";
  /** JSON body (POST). Serialized with `JSON.stringify`. */
  body?: unknown;
  /** Query-string params; null/undefined values are dropped. */
  params?: QueryParams;
  signal?: AbortSignal;
}

function buildUrl(path: string, params?: QueryParams): string {
  const url = `${API_BASE}${path}`;
  if (!params) {
    return url;
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `${url}?${qs}` : url;
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }
  return `Request failed (${status})`;
}

/**
 * The single authenticated fetch entry point. Returns the parsed JSON typed as
 * `T` (the caller's `ServerEnvelope`). Throws {@link ApiError} on any non-2xx.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, params, signal } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(buildUrl(path, params), {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });

  if (response.status === 401) {
    // Token rejected — drop it and bounce to the gate (does not log the token).
    reject();
    throw new ApiError(401, "Token rejected");
  }

  const parsed = await parseBody(response);
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(response.status, parsed), parsed);
  }
  return parsed as T;
}

/** Convenience GET. */
export function get<T>(path: string, params?: QueryParams, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: "GET", params, signal });
}

/** Convenience POST with a JSON body. */
export function postJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: "POST", body, signal });
}
