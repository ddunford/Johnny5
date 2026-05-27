import { create } from "zustand";
import type { StateView } from "@/services/types";
import type { ConnectionStatus } from "@/services/connectionStatus";

interface StateStoreState {
  /** Latest consolidated state — null until the first snapshot/frame arrives. */
  snapshot: StateView | null;
  status: ConnectionStatus;
  /** Replace the snapshot (each `/ws/state` frame is the whole current state). */
  setSnapshot: (snapshot: StateView) => void;
  setStatus: (status: ConnectionStatus) => void;
  reset: () => void;
}

export const useStateStore = create<StateStoreState>((set) => ({
  snapshot: null,
  status: "connecting",
  setSnapshot: (snapshot) => set({ snapshot }),
  setStatus: (status) => set({ status }),
  reset: () => set({ snapshot: null, status: "connecting" }),
}));
