import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { clearToken } from "@/auth/tokenStore";
import { useConsciousnessStore } from "@/stores/consciousnessStore";
import { useStateStore } from "@/stores/stateStore";

// Unmount React trees and clear sessionStorage + the in-memory singletons (token
// store, live-stream stores) between tests so state never leaks across cases.
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  clearToken();
  useConsciousnessStore.getState().reset();
  useStateStore.getState().reset();
});
