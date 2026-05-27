import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import type { ConnectionStatus } from "@/services/connectionStatus";
import { ConsciousnessPanel } from "./ConsciousnessPanel";

function setStatus(status: ConnectionStatus): void {
  act(() => useConsciousnessStore.getState().setStatus(status));
}

function addThought(id: number, text: string): void {
  act(() => useConsciousnessStore.getState().addThought({ id, ts: "2026-05-20T09:05:00+00:00", text }));
}

describe("ConsciousnessPanel", () => {
  it("shows the attaching message while connecting and empty", () => {
    render(<ConsciousnessPanel />);
    expect(screen.getByText(/Attaching to his inner monologue/)).toBeInTheDocument();
  });

  it("shows the quiet message when connected but he has no thoughts", () => {
    setStatus("open");
    render(<ConsciousnessPanel />);
    expect(screen.getByText(/He hasn't had a thought yet/)).toBeInTheDocument();
  });

  it("renders the live thought feed in arrival order as a log", () => {
    setStatus("open");
    addThought(1, "I wonder what Dan is working on right now.");
    addThought(2, "Reflecting on my recall left me a little more settled.");

    render(<ConsciousnessPanel />);
    const log = screen.getByRole("log", { name: "Stream of consciousness" });
    expect(log).toHaveTextContent("I wonder what Dan is working on right now.");
    expect(log).toHaveTextContent("Reflecting on my recall left me a little more settled.");
  });

  it("surfaces the connection status", () => {
    setStatus("reconnecting");
    render(<ConsciousnessPanel />);
    expect(screen.getByRole("status")).toHaveTextContent("reconnecting");
  });
});
