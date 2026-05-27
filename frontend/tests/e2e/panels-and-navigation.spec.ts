import { expect, test } from "@playwright/test";

import { attach, collectPageErrors, gotoApp, PANELS, TOKEN } from "./support/app";

/**
 * TC-5b.2..5b.6 (render) + navigation. Attach once, walk every panel via the nav,
 * and assert each mounts its landmark (heading + the data-independent controls/roles)
 * with ZERO console errors. Assertions avoid data-dependent content so this is stable
 * whether the backend is seeded or fresh.
 */
test.describe("panels render + navigation", () => {
  test.skip(!TOKEN, "set JOHNNY_TOKEN (the .env WS_TOKEN) to run the authenticated app");

  test("every panel renders from the nav with no console errors", async ({ page }) => {
    const errors = collectPageErrors(page);
    await attach(page);

    for (const panel of PANELS) {
      await page.getByRole("navigation", { name: "Panels" }).getByText(panel.label, { exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${panel.path}$`));
      // exact: true — e.g. "Memory" otherwise also matches the "Episodic memory" sub-heading.
      await expect(page.getByRole("heading", { name: panel.heading, exact: true })).toBeVisible();
    }

    // Panel-specific landmarks (present regardless of data volume). The consciousness
    // content area is either the log (once thoughts backfill) or its empty state — both
    // count as "rendered"; asserting the log alone races the WS backfill.
    await page.getByRole("navigation", { name: "Panels" }).getByText("Consciousness", { exact: true }).click();
    await expect(
      page
        .getByRole("log", { name: "Stream of consciousness" })
        .or(page.getByText(/hasn't had a thought yet|Attaching to his inner monologue/)),
    ).toBeVisible();

    await page.getByRole("navigation", { name: "Panels" }).getByText("Memory", { exact: true }).click();
    await expect(page.getByLabel("Search episodic memory")).toBeVisible();
    await expect(page.getByLabel("Search semantic facts")).toBeVisible();

    await page.getByRole("navigation", { name: "Panels" }).getByText("Audit", { exact: true }).click();
    await expect(page.getByLabel("Filter by event type")).toBeVisible();

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("the Self panel is read-only — a labelled Phase-9 placeholder, no approval controls", async ({
    page,
  }) => {
    await attach(page);
    await page.getByRole("navigation", { name: "Panels" }).getByText("Self", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Self" })).toBeVisible();
    // The Phase-9 self-edit gate is a labelled placeholder, NOT a faked approve/reject flow.
    await expect(page.getByText(/arrives in Phase/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /approve|reject/i })).toHaveCount(0);
  });

  test("an unknown route redirects into the app (consciousness)", async ({ page }) => {
    await attach(page);
    await gotoApp(page, "/does-not-exist");
    await expect(page).toHaveURL(/\/consciousness$/);
    await expect(page.getByRole("heading", { name: "Stream of consciousness" })).toBeVisible();
  });
});
