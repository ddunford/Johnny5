import { expect, test } from "@playwright/test";

import { attach, collectPageErrors, gotoApp, nullishCrashes, PANELS, TOKEN } from "./support/app";

/**
 * TASK-5b.14 / TC-5b.10 — the MANDATORY fresh-load smoke. The bug class that contract
 * tests + mocked component tests CANNOT catch: a panel that blanks or throws
 * `Cannot read properties of undefined` on a real fresh/empty-Johnny response.
 *
 * Runs against an ACTUALLY-RUNNING stack returning REAL empty-state responses (NOT
 * route mocks): a fresh Johnny — no thoughts, never slept, null mood, seed identity v1,
 * empty memory/audit/goals. Each panel is loaded by a full page reload (cold first-paint
 * per route — the strictest path), and the WHOLE run is asserted to produce zero
 * console errors and zero null/undefined crashes.
 *
 * ENV: requires the empty-Johnny stack (its cognitive loop paused/not-started so the
 * empty state is stable while we load every panel) — coordinated via the lead/devops.
 */
test.describe("fresh-load smoke · empty Johnny", () => {
  test.skip(!TOKEN, "set JOHNNY_TOKEN (the .env WS_TOKEN) to run against the empty stack");

  test("every panel cold-loads against real empty responses with no blank/console error/crash", async ({
    page,
  }) => {
    // This is the EMPTY-Johnny smoke — it asserts fresh-state responses (no thoughts,
    // never slept, empty trails). Skip on a populated/deployed stack: per the 6b
    // decision A, the live-empty variant runs only against a genuinely-empty stack;
    // the empty render path is otherwise covered by the AuditPanel component test
    // (exact empty copy + no nullish crash) + the contract empty-fixture projection.
    const probe = await page.request.get("/api/v1/thoughts", { headers: { "X-Token": TOKEN } });
    const populated = probe.ok() && (((await probe.json()).thoughts ?? []).length > 0);
    test.skip(populated, "stack is populated — run the empty smoke against an empty-Johnny stack");

    const errors = collectPageErrors(page);
    await attach(page);

    // Cold first-paint of every panel route (sessionStorage keeps us past the gate).
    for (const panel of PANELS) {
      await gotoApp(page, panel.path);
      await expect(
        page.getByRole("heading", { name: panel.heading, exact: true }),
        `${panel.label} must render its heading (not a blank screen)`,
      ).toBeVisible();
    }

    // The real empty-state responses must render as friendly empties — proof the panels
    // handled the empty wire (no rows, null mood, seed-v1) rather than blanking/crashing.
    await gotoApp(page, "/consciousness");
    await expect(page.getByText(/hasn't had a thought yet|Attaching to his inner monologue/)).toBeVisible();

    await gotoApp(page, "/memory");
    await expect(page.getByText("No episodes yet.")).toBeVisible();
    await expect(page.getByText("No facts yet.")).toBeVisible();

    await gotoApp(page, "/audit");
    // The live bus feed AND the durable Action trail (the Core `action_log`, FC-1) both start
    // empty on a fresh Johnny — neither must blank or throw on its first-ever (zero-row) load
    // (the P5b crash-on-first-load class: `Cannot read properties of undefined`). The durable
    // trail's empty default is `{actions: []}` until a tool runs through the dispatch.
    await expect(page.getByText("Nothing on the bus yet.")).toBeVisible();
    await expect(page.getByTestId("audit-actions-empty")).toHaveText(
      "No actions yet — nothing has run through the dispatch.",
    );

    await gotoApp(page, "/state");
    await expect(page.getByText("He has never slept yet.")).toBeVisible();
    await expect(page.getByText("v1 (seed)")).toBeVisible();

    await gotoApp(page, "/self");
    await expect(page.getByRole("heading", { name: "Self" })).toBeVisible();
    await expect(page.getByText(/arrives in Phase/i)).toBeVisible();

    // The crux: zero console errors, and specifically none of the undefined/null
    // projection crashes the smoke exists to catch.
    const crashes = nullishCrashes(errors);
    expect(crashes, `null/undefined crashes:\n${crashes.join("\n")}`).toEqual([]);
    expect(errors, `console errors on the fresh-Johnny first paint:\n${errors.join("\n")}`).toEqual([]);
  });
});
