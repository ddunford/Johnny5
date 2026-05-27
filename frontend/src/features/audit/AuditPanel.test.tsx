import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
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
