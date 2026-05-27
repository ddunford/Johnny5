import { expect, type Page } from "@playwright/test";

/**
 * Shared E2E helpers. The token comes from the environment (the runner exports the
 * real `WS_TOKEN` from `.env` as `JOHNNY_TOKEN`) — never hard-coded or committed.
 */
export const TOKEN = process.env.JOHNNY_TOKEN ?? "";

/** The six panels: nav label + the route + a resilient "this rendered" locator. */
export const PANELS = [
  { label: "Conversation", path: "/conversation", heading: "Conversation" },
  // Nav label is "Consciousness" but the panel's own H2 reads "Stream of consciousness".
  { label: "Consciousness", path: "/consciousness", heading: "Stream of consciousness" },
  { label: "State", path: "/state", heading: "State" },
  { label: "Memory", path: "/memory", heading: "Memory" },
  { label: "Audit", path: "/audit", heading: "Audit" },
  { label: "Self", path: "/self", heading: "Self" },
] as const;

/**
 * Attach to Johnny through the REAL token gate (type token → "Attach"), landing in
 * the shell. Asserts the gate appears first (proving no token = no app), then that
 * the panel nav mounts (proving the token was accepted).
 */
/**
 * Navigate to an app route. Uses `domcontentloaded`, not the default `load`: this is
 * an SPA with a long-lived WebSocket, so waiting for the full load event / every
 * subresource can hang against the live URL.
 */
export async function gotoApp(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: "domcontentloaded" });
}

export async function attach(page: Page): Promise<void> {
  await gotoApp(page, "/");
  const field = page.getByLabel("Access token");
  await expect(field, "the token gate should render on a fresh load").toBeVisible();
  await field.fill(TOKEN);
  await page.getByRole("button", { name: "Attach" }).click();
  await expect(
    page.getByRole("navigation", { name: "Panels" }),
    "the shell nav should mount once the token is accepted",
  ).toBeVisible();
}

/**
 * Subscribe to every browser-console error + uncaught page error. The returned array
 * fills as the page runs; assert it's empty after exercising a panel. This is the
 * bug class (e.g. `Cannot read properties of undefined`) that contract + mocked
 * component tests can't catch — only a real render against real responses can.
 */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  page.on("pageerror", (err) => {
    errors.push(`pageerror: ${err.message}`);
  });
  return errors;
}

/** The undefined/null projection crashes the smoke exists to catch, anywhere in the log. */
export function nullishCrashes(errors: string[]): string[] {
  return errors.filter((e) =>
    /cannot read propert(y|ies) of (undefined|null)|cannot convert undefined or null to object|is not a function|is undefined/i.test(
      e,
    ),
  );
}
