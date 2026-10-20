/*
  The 90-second judge path, at 375 and 1280 px, with motion on: pick a match,
  Blow full time (above the fold), watch the Newsroom Wall replay, approve on
  the stand-in card, read an edition and its Why drawer, and let Verify
  recompute every hash. Then the same path once with the keyboard only.

  The path follows whichever run Blow full time opens, read from the served
  run file: the hand-written sample while it is the only run (the card asks
  for every Confirm row, then says nothing was sent), or a recorded pipeline
  run once one is listed first (the card says it was already approved, the
  recap carries the approved label, Verify finds the approved hash equal to
  the published one). The recorded project in playwright.config.ts runs this
  file on the rehearsal build with the recorded-run fixture listed first, so
  both branches run on every pull request.
*/
import type { Page } from "@playwright/test";
import { STANDIN, appReady, expect, runOnScreen, tabTo, test } from "./fixtures";
import { decidedMessage, editionLabel, lastStep } from "../src/lib/runs";
import type { Run } from "../src/lib/types";

async function blownRun(page: Page): Promise<Run> {
  await expect(page).toHaveURL(/#\/newsroom\/[a-z0-9-]+$/);
  return runOnScreen(page);
}

for (const viewport of [
  { width: 375, height: 812 },
  { width: 1280, height: 800 },
]) {
  test(`judge path at ${viewport.width} px`, async ({ page }) => {
    const started = Date.now();
    await page.setViewportSize(viewport);
    await page.goto("/");
    await appReady(page);
    await expect(page.locator('[data-view="home"] section[data-state="ready"]')).toBeVisible({ timeout: 20_000 });

    const blow = page.getByRole("button", { name: "Blow full time" });
    await expect(blow).toHaveCount(1);
    const box = await blow.boundingBox();
    expect(box, "Blow full time has a box").not.toBeNull();
    expect((box?.y ?? 0) + (box?.height ?? 0), "Blow full time is above the fold").toBeLessThanOrEqual(viewport.height);
    await blow.click();

    const run = await blownRun(page);
    const id = run.run_id;
    const preview = run.provenance === "design_preview";
    const steps = lastStep(run);
    await expect(page.locator("#wall-label")).toContainText(run.label);
    await expect(page.getByText(new RegExp(`Showing step \\d+ of ${steps}\\.`))).toBeVisible();
    await expect(page.getByText(`Showing every step, ${steps} in all.`)).toBeVisible({ timeout: 30_000 });

    await page.getByRole("link", { name: "Open the approval card" }).click();
    await expect(page).toHaveURL(new RegExp(`#/approve/${id}$`));
    await expect(page.locator("#approve-label")).toHaveText(run.label);
    await expect(page.getByText(STANDIN)).toBeVisible();
    const approve = page.getByRole("button", { name: "Approve", exact: true });
    if (preview) {
      await approve.click();
      await expect(page.getByText("Tick the referee sentence and the injury sentence before approving.")).toBeVisible();
      await expect(page.getByLabel(/Confirm the referee sentence/)).toBeFocused();
      await page.getByLabel(/Confirm the referee sentence/).check();
      await page.getByLabel(/Confirm the injury sentence/).check();
      await approve.click();
      await expect(page.getByText(/Nothing was approved or published\. This stand-in card has no decision channel/)).toBeVisible();
      await expect(page.getByText(/None of them received anything\./)).toBeVisible();
    } else {
      await expect(page.getByText(/This run is not waiting for approval: its bundle state is/)).toBeVisible();
      await approve.click();
      await expect(page.getByText(decidedMessage(run) ?? "a decided run carries a decided message")).toBeVisible();
    }

    await page.getByRole("link", { name: /Next: Read the editions/ }).click();
    await expect(page.locator("#recap-label")).toContainText(editionLabel(run));
    await page.getByRole("tab", { name: /^EL/ }).click();
    const panel = page.locator('[data-view="recap"] [role="tabpanel"]');
    await expect(panel.locator('p[lang="el"]')).toBeVisible();
    const sentence = panel.locator('p[lang="el"] a').first();
    await sentence.click();
    const drawer = page.getByRole("dialog", { name: "Why this sentence says this" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Gate-cleared only", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(sentence).toBeFocused();

    await page.getByRole("link", { name: /Next: Verify the hash/ }).click();
    await expect(page.getByText(/the bundle hash equals the hash on the approval card/)).toBeVisible({ timeout: 30_000 });
    if (preview) {
      await expect(page.getByText(/There is no approved hash to compare with the published one/)).toBeVisible();
    } else {
      /* Verify lists every run's bundle under its own label; this run's block holds its comparison. */
      const block = page.locator(`[data-view="verify"] section[data-run-id="${id}"]`);
      await expect(block.getByRole("heading", { name: run.label })).toBeVisible();
      await expect(block.getByText(/The approved hash equals the published hash/)).toBeVisible();
    }
    await expect(page.getByText(/Recomputed in your browser: (\d+) of \1 files match\./)).toBeVisible({ timeout: 30_000 });
    expect(Date.now() - started, "the path fits in 90 seconds").toBeLessThan(90_000);
  });
}

test("judge path with the keyboard only, at 1280 px", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-state="ready"]')).toBeVisible({ timeout: 20_000 });

  await tabTo(page, page.getByRole("button", { name: "Blow full time" }));
  await page.keyboard.press("Enter");
  const run = await blownRun(page);
  const id = run.run_id;

  await tabTo(page, page.getByRole("link", { name: "Open the approval card" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#/approve/${id}$`));

  if (run.provenance === "design_preview") {
    await tabTo(page, page.getByLabel(/Confirm the referee sentence/));
    await page.keyboard.press("Space");
    await tabTo(page, page.getByLabel(/Confirm the injury sentence/));
    await page.keyboard.press("Space");
    await tabTo(page, page.getByRole("button", { name: "Approve", exact: true }));
    await page.keyboard.press("Enter");
    await expect(page.getByText(/Nothing was approved or published/)).toBeVisible();
  } else {
    await tabTo(page, page.getByRole("button", { name: "Approve", exact: true }));
    await page.keyboard.press("Enter");
    await expect(page.getByText(/This report was already approved on/)).toBeVisible();
  }

  await tabTo(page, page.getByRole("link", { name: /Next: Read the editions/ }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#/recap/${id}$`));
  await tabTo(page, page.getByRole("tab", { name: /^EN/ }));
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^EL/ })).toBeFocused();
  await expect(page.getByRole("tab", { name: /^EL/ })).toHaveAttribute("aria-selected", "true");
  const sentence = page.locator('[data-view="recap"] p[lang="el"] a').first();
  await tabTo(page, sentence);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Why this sentence says this" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sentence).toBeFocused();

  await tabTo(page, page.getByRole("link", { name: /Next: Verify the hash/ }));
  await page.keyboard.press("Enter");
  await expect(page.getByText(/the bundle hash equals the hash on the approval card/)).toBeVisible({ timeout: 30_000 });
});
