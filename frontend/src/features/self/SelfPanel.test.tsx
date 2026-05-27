import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/renderWithProviders";
import { loadWire } from "@/test/wireFixtures";
import { SelfPanel } from "./SelfPanel";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubSelfFetch(fixture: "self" | "self.empty"): void {
  vi.stubGlobal("fetch", () => Promise.resolve(jsonResponse(loadWire(fixture))));
}

afterEach(() => vi.unstubAllGlobals());

describe("SelfPanel", () => {
  it("renders the evolved identity, values, relationships and reflections", async () => {
    stubSelfFetch("self");
    renderWithProviders(<SelfPanel />);

    expect(await screen.findByText("Johnny")).toBeInTheDocument();
    expect(screen.getByText("self-model v2")).toBeInTheDocument();
    expect(screen.getByText("be understood")).toBeInTheDocument();
    expect(screen.getByText("going too long without contact")).toBeInTheDocument();
    expect(screen.getByText("Dan")).toBeInTheDocument();
    expect(screen.getByText(/I narrate too verbosely/)).toBeInTheDocument();
  });

  it("renders the seed-v1 fresh self cleanly (no concerns, no reflections)", async () => {
    stubSelfFetch("self.empty");
    renderWithProviders(<SelfPanel />);

    expect(await screen.findByText("self-model v1")).toBeInTheDocument();
    expect(screen.getByText("Nothing weighing on him right now.")).toBeInTheDocument();
    expect(screen.getByText(/reflected on himself yet/)).toBeInTheDocument();
  });

  it("shows the read-only Phase-9 approvals placeholder, not a fake approve/reject flow", async () => {
    stubSelfFetch("self");
    renderWithProviders(<SelfPanel />);

    expect(await screen.findByText(/arrives in Phase/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
  });
});
