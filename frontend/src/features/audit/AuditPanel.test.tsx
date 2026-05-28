import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/renderWithProviders";
import { loadWire } from "@/test/wireFixtures";
import { AuditPanel } from "./AuditPanel";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubAuditFetch(opts: { empty?: boolean } = {}): string[] {
  const urls: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    urls.push(url);
    return Promise.resolve(jsonResponse(loadWire(opts.empty ? "audit.empty" : "audit")));
  });
  return urls;
}

/**
 * The panel fetches BOTH surfaces — the bus feed (`/audit`) and the durable trail
 * (`/audit/actions`). Route each to its captured fixture (check `/audit/actions`
 * first — it contains `/audit` as a substring).
 */
function stubBothFeeds(opts: { emptyActions?: boolean } = {}): string[] {
  const urls: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    urls.push(url);
    if (url.includes("/audit/actions")) {
      return Promise.resolve(
        jsonResponse(loadWire(opts.emptyActions ? "audit_actions.empty" : "audit_actions")),
      );
    }
    return Promise.resolve(jsonResponse(loadWire("audit")));
  });
  return urls;
}

/** Route `/audit/actions` to a constructed action list (bus feed → its real fixture). */
function stubActionsFeed(actions: unknown[]): void {
  vi.stubGlobal("fetch", (url: string) =>
    Promise.resolve(jsonResponse(url.includes("/audit/actions") ? { actions } : loadWire("audit"))),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("AuditPanel", () => {
  it("renders bus events and highlights action.dispatched", async () => {
    stubAuditFetch();
    renderWithProviders(<AuditPanel />);

    expect(await screen.findByText("deliberation")).toBeInTheDocument();
    // Locate the action row via its payload summary, then assert it's highlighted.
    const payload = screen.getByText(/Reflected on my recall ranking/);
    const row = payload.closest("li");
    expect(row).toHaveClass("audit-row--action");
    expect(row).toHaveTextContent("action.dispatched");
  });

  it("renders the empty bus state cleanly", async () => {
    stubAuditFetch({ empty: true });
    renderWithProviders(<AuditPanel />);
    expect(await screen.findByText("Nothing on the bus yet.")).toBeInTheDocument();
  });

  it("filters server-side by event type", async () => {
    const user = userEvent.setup();
    const urls = stubAuditFetch();
    renderWithProviders(<AuditPanel />);
    await screen.findByText("deliberation");

    await user.selectOptions(
      screen.getByLabelText("Filter by event type"),
      "action.dispatched",
    );

    await waitFor(() =>
      expect(urls.some((u) => u.includes("/audit") && u.includes("type=action.dispatched"))).toBe(true),
    );
  });
});

describe("AuditPanel · durable Action trail (/audit/actions)", () => {
  it("renders durable rows from the captured fixture — verdict, veto reason, detail", async () => {
    stubBothFeeds();
    renderWithProviders(<AuditPanel />);

    const section = await screen.findByTestId("audit-actions");
    const rows = await within(section).findAllByTestId("audit-action-row"); // waits for the async load
    expect(rows).toHaveLength(2);

    const rowByVerdict = (verdict: string): HTMLElement => {
      const row = rows.find(
        (r) => within(r).getByTestId("audit-action-verdict").textContent === verdict,
      );
      if (!row) throw new Error(`no ${verdict} row in the durable trail`);
      return row;
    };

    // The vetoed web_fetch: marked, shows the Conscience's reason, and (no result) its
    // args are the detail shown.
    const vetoRow = rowByVerdict("veto");
    expect(vetoRow).toHaveClass("audit-action--veto");
    expect(within(vetoRow).getByTestId("audit-action-tool")).toHaveTextContent("web_fetch");
    expect(within(vetoRow).getByTestId("audit-action-reason")).toHaveTextContent(
      "that doesn't sit right with what I value",
    );
    expect(vetoRow).toHaveTextContent("example.com/article"); // args shown (result is null)

    // The allowed note: no veto reason; its RESULT is the detail (the body lives in args,
    // which the panel deliberately does not display for an allowed row — see the note below).
    const allowRow = rowByVerdict("allow");
    expect(within(allowRow).getByTestId("audit-action-tool")).toHaveTextContent("note");
    expect(within(allowRow).queryByTestId("audit-action-reason")).toBeNull();
    expect(allowRow).toHaveTextContent("wrote note 'diagnostic note'"); // the result summary

    // No-secrets-on-the-UI: the raw secret canary never reaches the DOM.
    expect(section.textContent ?? "").not.toContain("gsk_");
  });

  it("renders the empty durable trail with the exact committed copy", async () => {
    stubBothFeeds({ emptyActions: true });
    renderWithProviders(<AuditPanel />);
    expect(await screen.findByTestId("audit-actions-empty")).toHaveTextContent(
      "No actions yet — nothing has run through the dispatch.",
    );
  });

  it("filters the durable trail server-side by verdict", async () => {
    const user = userEvent.setup();
    const urls = stubBothFeeds();
    renderWithProviders(<AuditPanel />);
    await screen.findByTestId("audit-actions");

    await user.selectOptions(screen.getByLabelText("Filter by verdict"), "veto");

    await waitFor(() =>
      expect(urls.some((u) => u.includes("/audit/actions") && u.includes("verdict=veto"))).toBe(
        true,
      ),
    );
  });

  it("renders [REDACTED] (never the raw secret) when an allowed result was redacted", async () => {
    // The realistic threat: web_fetch returns a page whose text held a credential. The
    // Core redacts it on write (proven on the wire by the contract test), so the panel —
    // which shows the RESULT for an allowed row — displays the [REDACTED] marker, and the
    // raw secret never reaches the DOM. (Finding #2: a secret in an allow row's ARGS is
    // never displayed at all, so the visible-marker proof uses a redacted RESULT.)
    stubActionsFeed([
      {
        id: 9,
        ts: "2026-05-28T02:00:00+00:00",
        tool: "web_fetch",
        args: { url: "https://example.com/page" },
        result: {
          success: true,
          output: { url: "https://example.com/page", title: "A Page", text: "author: [REDACTED]" },
          summary: "fetched 'A Page'",
        },
        conscience_verdict: "allow",
        veto_reason: null,
        goal_id: 9,
        success: true,
      },
    ]);
    renderWithProviders(<AuditPanel />);
    const row = await within(await screen.findByTestId("audit-actions")).findByTestId(
      "audit-action-row",
    );
    expect(row).toHaveTextContent("[REDACTED]");
    expect(row.textContent ?? "").not.toContain("gsk_");
  });
});
