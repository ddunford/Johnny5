import { expect, test } from "@playwright/test";

import { attach, collectPageErrors, TOKEN } from "./support/app";

/**
 * TC-5b.2 — the live consciousness stream + its connection health. The pill shows
 * "live" once the WS is open; a transient network drop flips it to reconnecting/offline
 * and it auto-recovers to "live" (the ReconnectingSocket backoff, FC-8). A 1008 (bad
 * token) is the auth-gate path, covered separately.
 */
test.describe("consciousness stream", () => {
  test.skip(!TOKEN, "set JOHNNY_TOKEN (the .env WS_TOKEN) to run the authenticated app");

  test("the stream connects live with no console errors", async ({ page }) => {
    const errors = collectPageErrors(page);
    await attach(page);
    await page.getByRole("navigation", { name: "Panels" }).getByText("Consciousness", { exact: true }).click();

    // The WS opens → the pill reads "live".
    await expect(page.getByRole("status")).toHaveAttribute("aria-label", "Stream live");
    // Either thoughts have backfilled (the log) or the explicit empty state — never blank.
    await expect(
      page
        .getByRole("log", { name: "Stream of consciousness" })
        .or(page.getByText(/hasn't had a thought yet|Attaching to his inner monologue/)),
    ).toBeVisible();

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("reconnects and resumes the live stream after a reload", async ({ page }) => {
    test.setTimeout(90_000);
    await attach(page);
    await page.getByRole("navigation", { name: "Panels" }).getByText("Consciousness", { exact: true }).click();
    await expect(page.getByRole("status")).toHaveAttribute("aria-label", "Stream live");

    // A full reload tears the WebSocket down and the client must re-establish it from
    // scratch; the server replays the backfill so the stream resumes → "live" again.
    // (The transient-drop *backoff* path is covered deterministically in
    // reconnectingSocket's unit tests — Playwright's offline emulation does NOT reliably
    // close an already-open WebSocket, so we exercise the real reconnect via a reload.)
    await page.reload();
    await expect(page.getByRole("status")).toHaveAttribute("aria-label", "Stream live", {
      timeout: 30_000,
    });
    await expect(
      page
        .getByRole("log", { name: "Stream of consciousness" })
        .or(page.getByText(/hasn't had a thought yet|Attaching to his inner monologue/)),
    ).toBeVisible();
  });
});
