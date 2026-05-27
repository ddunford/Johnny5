/**
 * Selector hooks over the live Zustand stores, plus {@link useLiveStreams} which
 * opens both WS connections for the lifetime of the authenticated session.
 *
 * The streams are attached once (at app root, inside the token gate) rather than
 * per-panel, so the consciousness feed keeps capturing thoughts even when you're
 * on another panel — that's what lets the Conversation panel correlate his reply
 * to a message you sent while looking elsewhere.
 */

import { useEffect } from "react";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import { useStateStore } from "@/stores/stateStore";
import { acquireConsciousnessStream } from "@/services/consciousnessSocket";
import { acquireStateStream } from "@/services/stateSocket";
import type { ConnectionStatus } from "@/services/connectionStatus";
import type { StateView, Thought } from "@/services/types";

export function useLiveStreams(): void {
  useEffect(() => {
    const releaseConsciousness = acquireConsciousnessStream();
    const releaseState = acquireStateStream();
    return () => {
      releaseConsciousness();
      releaseState();
    };
  }, []);
}

export function useThoughtsFeed(): Thought[] {
  return useConsciousnessStore((state) => state.thoughts);
}

export function useConsciousnessStatus(): ConnectionStatus {
  return useConsciousnessStore((state) => state.status);
}

export function useLiveState(): StateView | null {
  return useStateStore((state) => state.snapshot);
}

export function useStateStreamStatus(): ConnectionStatus {
  return useStateStore((state) => state.status);
}
