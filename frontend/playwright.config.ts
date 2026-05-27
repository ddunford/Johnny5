import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E + the fresh-load smoke (TASK-5b.13 / 5b.14). These run against an
 * ACTUALLY-RUNNING stack with REAL backend responses (NOT route mocks) — by default
 * the Traefik-served SPA, overridable for a local `vite dev` run.
 *
 *   PLAYWRIGHT_BASE_URL   target origin (default: the served stack)
 *   JOHNNY_TOKEN          the shared WS_TOKEN (export from .env; never committed)
 *
 * Artifacts stay OUT of the repo root per CLAUDE.md Playwright hygiene: `outputDir`
 * and the HTML report both land under gitignored paths (`test-results/`,
 * `playwright-report/`). The stack is shared + stateful (one live Mind), so we run
 * serially (workers: 1) to avoid two specs perturbing the same loop.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "https://johnny.demosrv.uk",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "off",
    ignoreHTTPSErrors: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
