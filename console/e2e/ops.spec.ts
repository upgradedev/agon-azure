/*
  The Media office view on the recorded run: status, the spans between the
  job executions read from Azure, delivery, checks and the approval audit
  trail with the published hash recomputed in the browser. Checked with axe
  and screenshotted at 375 and 1280.
*/
import { appReady, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";

for (const viewport of [
  { width: 375, height: 812 },
  { width: 1280, height: 800 },
]) {
  test(`media office at ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await page.goto("/#/media-office");
    await appReady(page);
    await expect(page.locator('[data-view="media-office"] [data-ops="done"]')).toBeAttached({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: "What the newsroom did for this match" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "The approval survived the process ending" })).toBeVisible();
    await expect(page.getByText(/The fleet worker that posted the approval card ended at 21:21:36 UTC\./)).toBeVisible();
    await expect(page.getByText(/was verified and recorded at 21:24:58 UTC \(the intake job, which verified it, ran from 21:24:36 UTC to 21:25:27 UTC\)/)).toBeVisible();
    await expect(page.getByText(/A new fleet worker process started at 21:25:06 UTC/)).toBeVisible();
    await expect(page.getByText("Decision verified to published")).toBeVisible();
    await expect(page.getByText("Approved hash equals published hash")).toBeVisible();
    await expect(page.getByText("not measured yet", { exact: true })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectNoSeriousAxe(page, `media office ${viewport.width}`);
    await page.screenshot({ path: `screenshots/35-media-office-${viewport.width}-light.png`, fullPage: true });
  });
}

test("About says why a club buys this, gaps included", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#/about");
  await appReady(page);
  await expect(page.getByRole("heading", { name: "Why a club buys this" })).toBeVisible();
  await expect(page.getByText("The residual gaps")).toBeVisible();
  await expect(page.getByText(/cost per match is not measured yet/)).toBeVisible();
});
