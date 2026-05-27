import { expect, test } from "@playwright/test";

import { attach, collectPageErrors, gotoApp, TOKEN } from "./support/app";

/**
 * TC-5b.7 — the token gate, driven from the UI against the real backend.
 * No/wrong token loads NOTHING; the correct token attaches; a server rejection
 * (401/1008) routes back to the gate. Token in sessionStorage, never in a URL.
 */
test.describe("auth gate", () => {
  test("a fresh load with no token shows the gate and mounts no panels", async ({ page }) => {
    await gotoApp(page, "/");
    await expect(page.getByLabel("Access token")).toBeVisible();
    await expect(page.getByRole("button", { name: "Attach" })).toBeVisible();
    // The app shell (and therefore every data-fetching panel) must NOT be mounted.
    await expect(page.getByRole("navigation", { name: "Panels" })).toHaveCount(0);
  });

  test("a deep link while unauthenticated still shows the gate, not the panel", async ({ page }) => {
    await gotoApp(page, "/state");
    await expect(page.getByLabel("Access token")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Panels" })).toHaveCount(0);
  });

  test("a blank token is rejected client-side (no request)", async ({ page }) => {
    await gotoApp(page, "/");
    await page.getByRole("button", { name: "Attach" }).click();
    // exact: true — the gate subtitle also contains "Enter the access token to attach…".
    await expect(page.getByText("Enter the access token", { exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Panels" })).toHaveCount(0);
  });

  test("a wrong token is rejected by the server and routes back to the gate", async ({ page }) => {
    await gotoApp(page, "/");
    await page.getByLabel("Access token").fill("definitely-not-the-token");
    await page.getByRole("button", { name: "Attach" }).click();
    // The first authenticated call 401s (or the WS closes 1008) → token cleared → gate
    // reappears with the rejection notice.
    await expect(page.getByText("That token was rejected. Try again.")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Panels" })).toHaveCount(0);
  });

  test("the correct token attaches and stores the token in sessionStorage (not localStorage)", async ({
    page,
  }) => {
    test.skip(!TOKEN, "set JOHNNY_TOKEN (the .env WS_TOKEN) to run the authenticated path");
    const errors = collectPageErrors(page);
    await attach(page);

    const stored = await page.evaluate(() => ({
      session: window.sessionStorage.getItem("johnny5.token"),
      local: window.localStorage.getItem("johnny5.token"),
    }));
    expect(stored.session, "token must live in sessionStorage").toBeTruthy();
    expect(stored.local, "token must NOT be in localStorage").toBeNull();
    // The token must never leak into the page URL.
    expect(page.url()).not.toContain(TOKEN);
    expect(errors, errors.join("\n")).toEqual([]);
  });
});
