import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { clearToken } from "@/auth/tokenStore";

// Unmount React trees and clear sessionStorage + the in-memory token-store
// singleton between tests so token-gate and store state never leak across cases.
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  clearToken();
});
