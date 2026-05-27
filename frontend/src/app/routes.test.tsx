import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { makeTestQueryClient } from "@/test/renderWithProviders";
import { AppRoutes } from "./routes";

function renderAt(path: string): void {
  render(
    <QueryClientProvider client={makeTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // Panels that read via React Query fire on mount; stub the network so they
  // settle quietly (this suite only asserts routing, not panel data).
  vi.stubGlobal("fetch", () => Promise.reject(new Error("no network in test")));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppRoutes", () => {
  it("renders the shell nav with all six panels", () => {
    renderAt("/consciousness");
    const nav = screen.getByRole("navigation", { name: "Panels" });
    for (const label of ["Conversation", "Consciousness", "State", "Memory", "Audit", "Self"]) {
      expect(nav).toHaveTextContent(label);
    }
  });

  it("redirects the index to the consciousness panel", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { name: "Stream of consciousness" })).toBeInTheDocument();
  });

  it("redirects unknown routes to the consciousness panel", () => {
    renderAt("/nonsense");
    expect(screen.getByRole("heading", { name: "Stream of consciousness" })).toBeInTheDocument();
  });

  it("routes to the state panel", () => {
    renderAt("/state");
    expect(screen.getByRole("heading", { name: "State" })).toBeInTheDocument();
  });
});
