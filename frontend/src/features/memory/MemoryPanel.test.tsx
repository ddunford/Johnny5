import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/renderWithProviders";
import { loadWire } from "@/test/wireFixtures";
import { MemoryPanel } from "./MemoryPanel";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubMemoryFetch(opts: { empty?: boolean } = {}): string[] {
  const urls: string[] = [];
  vi.stubGlobal("fetch", (url: string) => {
    urls.push(url);
    if (url.includes("/memory/episodes")) {
      return Promise.resolve(jsonResponse(loadWire(opts.empty ? "memory_episodes.empty" : "memory_episodes")));
    }
    if (url.includes("/memory/facts")) {
      return Promise.resolve(jsonResponse(loadWire(opts.empty ? "memory_facts.empty" : "memory_facts")));
    }
    return Promise.resolve(jsonResponse({}));
  });
  return urls;
}

afterEach(() => vi.unstubAllGlobals());

describe("MemoryPanel", () => {
  it("renders recent episodes and semantic facts", async () => {
    stubMemoryFetch();
    renderWithProviders(<MemoryPanel />);

    expect(
      await screen.findByText("Dan asked me about how my pgvector recall ranking works."),
    ).toBeInTheDocument();
    // Fact triple split across nodes — match within the list item.
    expect(await screen.findByText("pgvector")).toBeInTheDocument();
    expect(screen.getByText("semantic recall")).toBeInTheDocument();
  });

  it("renders the empty memory state cleanly", async () => {
    stubMemoryFetch({ empty: true });
    renderWithProviders(<MemoryPanel />);

    expect(await screen.findByText("No episodes yet.")).toBeInTheDocument();
    expect(await screen.findByText("No facts yet.")).toBeInTheDocument();
  });

  it("sends the search query to the episodes endpoint", async () => {
    const user = userEvent.setup();
    const urls = stubMemoryFetch();
    renderWithProviders(<MemoryPanel />);
    await screen.findByText("Dan asked me about how my pgvector recall ranking works.");

    await user.type(screen.getByLabelText("Search episodic memory"), "pgvector");

    await waitFor(() =>
      expect(urls.some((u) => u.includes("/memory/episodes") && u.includes("q=pgvector"))).toBe(true),
    );
  });
});
