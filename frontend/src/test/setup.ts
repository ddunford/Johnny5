import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees and clear sessionStorage between tests so token-gate and
// store state never leaks across cases.
afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});
