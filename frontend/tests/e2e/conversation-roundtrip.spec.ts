import { expect, test } from "@playwright/test";

import { attach, collectPageErrors, TOKEN } from "./support/app";

/**
 * TC-5b.1 — the conversation round-trip (the headline "talk to him" flow). You speak;
 * he doesn't reply synchronously — your message becomes a percept he thinks about, and
 * his response emerges as a NEW thought on `/ws/consciousness` a tick later (SPEC §7).
 *
 * Requires a LIVE loop + reachable inference (so a percept produces a thought). Run
 * against the seeded/lived-in stack, NOT the paused empty-Johnny used for the smoke.
 */
test.describe("conversation round-trip", () => {
  test.skip(!TOKEN, "set JOHNNY_TOKEN (the .env WS_TOKEN) to run the authenticated app");

  test("empty message is blocked client-side (no send)", async ({ page }) => {
    await attach(page);
    await page.getByRole("navigation", { name: "Panels" }).getByText("Conversation", { exact: true }).click();
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("Type something to say to him")).toBeVisible();
    // Nothing was sent — the "You said" log never appears.
    await expect(page.getByRole("heading", { name: "You said" })).toHaveCount(0);
  });

  test("sending acks optimistically, then his thought surfaces on the stream", async ({ page }) => {
    test.setTimeout(180_000); // a real tick + narrator inference can take many seconds
    const errors = collectPageErrors(page);
    await attach(page);
    await page.getByRole("navigation", { name: "Panels" }).getByText("Conversation", { exact: true }).click();

    const probe = `e2e round-trip probe ${Date.now()}`;
    await page.getByLabel("Message to Johnny").fill(probe);
    await page.getByRole("button", { name: "Send" }).click();

    // 1. Optimistic ack: the message is echoed under "You said" with "he heard you"
    //    (the 202 {accepted,queue_depth}) — NOT a synchronous reply.
    await expect(page.getByText(probe)).toBeVisible();
    await expect(page.getByText(/he heard you/)).toBeVisible();
    // No hung "awaiting reply" spinner — the send control is usable again.
    await expect(page.getByRole("button", { name: "Send" })).toBeEnabled();

    // 2. The round-trip: a NEW thought emerges after the message and surfaces under
    //    "What he's thought since" (he thought ABOUT the percept). Content is the
    //    narrator's — non-deterministic — so we assert a response appears, not its text.
    await expect(page.locator(".convo-response").first()).toBeVisible({ timeout: 150_000 });
    await expect(
      page.getByText("He hasn't had a new thought yet — give him a tick."),
    ).toHaveCount(0);

    expect(errors, errors.join("\n")).toEqual([]);
  });
});
