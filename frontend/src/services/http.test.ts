import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, get, postJson, request } from "./http";
import { getToken, setToken } from "@/auth/tokenStore";

interface FetchCall {
  url: string;
  init: RequestInit;
}

function mockFetch(
  responder: (call: FetchCall) => { status: number; body?: unknown },
): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal("fetch", (url: string, init: RequestInit = {}) => {
    const call = { url, init };
    calls.push(call);
    const { status, body } = responder(call);
    const text = body === undefined ? "" : JSON.stringify(body);
    return Promise.resolve(
      new Response(text, { status, headers: { "Content-Type": "application/json" } }),
    );
  });
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("http.request", () => {
  it("prefixes the API base and returns parsed JSON", async () => {
    const calls = mockFetch(() => ({ status: 200, body: { ok: true } }));
    const result = await get<{ ok: boolean }>("/state");
    expect(result).toEqual({ ok: true });
    expect(calls[0].url).toBe("/api/v1/state");
  });

  it("injects the bearer token from the token store", async () => {
    setToken("tok-123");
    const calls = mockFetch(() => ({ status: 200, body: {} }));
    await get("/thoughts");
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");
  });

  it("omits the Authorization header when there is no token", async () => {
    const calls = mockFetch(() => ({ status: 200, body: {} }));
    await get("/state");
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("serializes query params and drops null/undefined", async () => {
    const calls = mockFetch(() => ({ status: 200, body: {} }));
    await get("/audit", { limit: 100, type: "thought", missing: null });
    expect(calls[0].url).toBe("/api/v1/audit?limit=100&type=thought");
  });

  it("sends a JSON body on POST", async () => {
    const calls = mockFetch(() => ({ status: 202, body: { accepted: true } }));
    await postJson("/input", { text: "hi" });
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(JSON.stringify({ text: "hi" }));
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("application/json");
  });

  it("on 401 clears the token and throws ApiError(401)", async () => {
    setToken("bad-token");
    expect(getToken()).toBe("bad-token");
    mockFetch(() => ({ status: 401, body: { detail: "invalid or missing token" } }));

    await expect(request("/state")).rejects.toMatchObject({ status: 401 });
    expect(getToken()).toBeNull();
  });

  it("maps a non-2xx error to ApiError with the server detail", async () => {
    mockFetch(() => ({ status: 429, body: { detail: "queue full" } }));
    const error = await request("/input", { method: "POST", body: { text: "x" } }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(429);
    expect((error as ApiError).message).toBe("queue full");
  });
});
