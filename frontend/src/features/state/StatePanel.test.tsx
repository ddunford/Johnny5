import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/renderWithProviders";
import { useStateStore } from "@/stores/stateStore";
import { adaptStateFrame, type StateFrame } from "@/services/wsFrames";
import type { StateView } from "@/services/types";
import { loadWire } from "@/test/wireFixtures";
import { StatePanel } from "./StatePanel";

function seed(view: StateView): void {
  act(() => useStateStore.getState().setSnapshot(view));
}

function fromFixture(name: string): StateView {
  return adaptStateFrame(loadWire<StateFrame>(name));
}

beforeEach(() => {
  // The panel also fires the REST snapshot query; stub it away so it settles
  // quietly (the live store snapshot is what these tests assert on).
  vi.stubGlobal("fetch", () => Promise.reject(new Error("no network in test")));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StatePanel", () => {
  it("renders the live dashboard from a populated state frame", () => {
    seed(fromFixture("ws_state"));
    renderWithProviders(<StatePanel />);

    // 7 drives.
    for (const name of ["curiosity", "boredom", "connection", "mastery", "coherence", "energy", "continuity"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByText("calm and content, with a thread of curiosity")).toBeInTheDocument();
    expect(
      screen.getByText("Understand how my own recall ranking blends similarity and recency"),
    ).toBeInTheDocument();
    expect(screen.getByText("Awake")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText(/Last slept/)).toBeInTheDocument();
    expect(screen.queryByText("⚠ DEGRADED")).not.toBeInTheDocument();
  });

  it("renders the fresh/empty Mind cleanly (null mood, no goal, never slept)", () => {
    seed(fromFixture("ws_state.empty"));
    renderWithProviders(<StatePanel />);

    expect(screen.getByText(/No mood yet/)).toBeInTheDocument();
    expect(screen.getByText("No active goal right now.")).toBeInTheDocument();
    expect(screen.getByText("He has never slept yet.")).toBeInTheDocument();
    expect(screen.getByText("v1 (seed)")).toBeInTheDocument();
    expect(screen.getByText("Awake")).toBeInTheDocument();
    // Drives still render at their setpoints.
    expect(screen.getByText("continuity")).toBeInTheDocument();
  });

  it("shows the DEGRADED flag when full_agency is false", () => {
    const view = fromFixture("ws_state");
    seed({ ...view, sleep: { ...view.sleep, full_agency: false } });
    renderWithProviders(<StatePanel />);
    expect(screen.getByText("⚠ DEGRADED")).toBeInTheDocument();
  });
});
