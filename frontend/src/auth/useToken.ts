import { useSyncExternalStore } from "react";
import {
  clearToken,
  getSnapshot,
  setToken,
  subscribe,
  type TokenState,
} from "./tokenStore";

export interface UseToken extends TokenState {
  setToken: (token: string) => void;
  clearToken: () => void;
}

/**
 * React binding over {@link tokenStore}. Components re-render when the token is
 * set, cleared, or rejected by the server.
 */
export function useToken(): UseToken {
  const state = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { ...state, setToken, clearToken };
}
