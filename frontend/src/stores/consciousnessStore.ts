import { create } from "zustand";
import type { Thought } from "@/services/types";
import type { ConnectionStatus } from "@/services/connectionStatus";

/** Cap the in-memory feed so a long-running tab doesn't grow without bound. */
const MAX_THOUGHTS = 1000;

interface ConsciousnessState {
  /** Thoughts in arrival (chronological) order. */
  thoughts: Thought[];
  status: ConnectionStatus;
  /** Add a thought; deduped by id (server replays backfill on every reconnect). */
  addThought: (thought: Thought) => void;
  setStatus: (status: ConnectionStatus) => void;
  reset: () => void;
}

export const useConsciousnessStore = create<ConsciousnessState>((set) => ({
  thoughts: [],
  status: "connecting",
  addThought: (thought) =>
    set((state) => {
      if (state.thoughts.some((existing) => existing.id === thought.id)) {
        return state;
      }
      const next = [...state.thoughts, thought];
      return { thoughts: next.length > MAX_THOUGHTS ? next.slice(-MAX_THOUGHTS) : next };
    }),
  setStatus: (status) => set({ status }),
  reset: () => set({ thoughts: [], status: "connecting" }),
}));
