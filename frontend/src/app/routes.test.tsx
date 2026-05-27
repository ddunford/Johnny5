import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "./routes";

function renderAt(path: string): void {
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

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

  it("routes to each panel", () => {
    renderAt("/state");
    expect(screen.getByRole("heading", { name: "State" })).toBeInTheDocument();
  });
});
