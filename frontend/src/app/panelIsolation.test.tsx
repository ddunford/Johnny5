import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { PanelBoundary } from "@/components/PanelBoundary";

function Bomb(): React.ReactNode {
  throw new Error("panel exploded");
}

/**
 * The CLAUDE.md guarantee: a dead panel must not kill the rest of the app. This
 * mounts a crashing panel inside its route-level PanelBoundary (exactly how
 * AppRoutes wraps every panel) and asserts the shell chrome (nav) survives and
 * only the offending panel is replaced by its fallback.
 */
describe("panel isolation", () => {
  it("contains a crashing panel to its own boundary; the shell + nav survive", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <MemoryRouter initialEntries={["/boom"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route
              path="boom"
              element={
                <PanelBoundary name="Consciousness">
                  <Bomb />
                </PanelBoundary>
              }
            />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    // The shell chrome is intact.
    expect(screen.getByRole("navigation", { name: "Panels" })).toBeInTheDocument();
    // The crashing panel shows its isolated fallback, not a white screen.
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Consciousness");
    expect(alert).toHaveTextContent("panel exploded");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
