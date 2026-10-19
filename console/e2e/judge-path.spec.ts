/*
  The 90-second judge path, at 375 and 1280 px, with motion on: pick a match,
  Blow full time (above the fold), watch the Newsroom Wall replay to
  "awaiting approval", approve on the stand-in card, read an edition and its
  Why drawer, and let Verify recompute every hash. Then the same path once
  with the keyboard only.
*/
import { LABEL, STANDIN, appReady, expect, tabTo, test } from "./fixtures";

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

    await expect(page).toHaveURL(/#\/newsroom\/sample-s1$/);
    await expect(page.locator("#wall-label")).toContainText(LABEL);
    await expect(page.getByText(/Showing step \d+ of 12\./)).toBeVisible();
    await expect(page.getByText("Showing every step, 12 in all.")).toBeVisible({ timeout: 30_000 });

    await page.getByRole("link", { name: "Open the approval card" }).click();
    await expect(page).toHaveURL(/#\/approve\/sample-s1$/);
    await expect(page.locator("#approve-label")).toHaveText(LABEL);
    await expect(page.getByText(STANDIN)).toBeVisible();
    const approve = page.getByRole("button", { name: "Approve", exact: true });
    await approve.click();
    await expect(page.getByText("Tick the referee sentence and the injury sentence before approving.")).toBeVisible();
    await expect(page.getByLabel(/Confirm the referee sentence/)).toBeFocused();
    await page.getByLabel(/Confirm the referee sentence/).check();
    await page.getByLabel(/Confirm the injury sentence/).check();
    await approve.click();
    await expect(page.getByText(/Nothing was approved or published\. The decision endpoint is not deployed yet/)).toBeVisible();
    await expect(page.getByText(/None of them received anything\./)).toBeVisible();

    await page.getByRole("link", { name: /Next: Read the editions/ }).click();
    await expect(page.locator("#recap-label")).toContainText(`Not approved. ${LABEL}`);
    await page.getByRole("tab", { name: /^EL/ }).click();
    const panel = page.locator('[data-view="recap"] [role="tabpanel"]');
    await expect(panel.locator('p[lang="el"]')).toBeVisible();
    const sentence = panel.locator('p[lang="el"] button').first();
    await sentence.click();
    const drawer = page.getByRole("dialog", { name: "Why this sentence says this" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Gate-cleared only", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(sentence).toBeFocused();

    await page.getByRole("link", { name: /Next: Verify the hash/ }).click();
    await expect(page.getByText(/the bundle hash equals the hash on the approval card/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/There is no approved hash to compare with the published one/)).toBeVisible();
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
  await expect(page).toHaveURL(/#\/newsroom\/sample-s1$/);

  await tabTo(page, page.getByRole("link", { name: "Open the approval card" }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/approve\/sample-s1$/);

  await tabTo(page, page.getByLabel(/Confirm the referee sentence/));
  await page.keyboard.press("Space");
  await tabTo(page, page.getByLabel(/Confirm the injury sentence/));
  await page.keyboard.press("Space");
  await tabTo(page, page.getByRole("button", { name: "Approve", exact: true }));
  await page.keyboard.press("Enter");
  await expect(page.getByText(/Nothing was approved or published/)).toBeVisible();

  await tabTo(page, page.getByRole("link", { name: /Next: Read the editions/ }));
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/recap\/sample-s1$/);
  await tabTo(page, page.getByRole("tab", { name: /^EN/ }));
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /^EL/ })).toBeFocused();
  await expect(page.getByRole("tab", { name: /^EL/ })).toHaveAttribute("aria-selected", "true");
  const sentence = page.locator('[data-view="recap"] p[lang="el"] button').first();
  await tabTo(page, sentence);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "Why this sentence says this" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sentence).toBeFocused();

  await tabTo(page, page.getByRole("link", { name: /Next: Verify the hash/ }));
  await page.keyboard.press("Enter");
  await expect(page.getByText(/the bundle hash equals the hash on the approval card/)).toBeVisible({ timeout: 30_000 });
});
