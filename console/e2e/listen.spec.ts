/*
  PR C: listening and polish. Listen mode on the recap (the recorded run's
  audio per edition, its disclosure and captions, the Greek goal line said
  honestly), Pitch Sound on the replay (started only by a tap, with a mute),
  and the Newsroom Wall stepper through the whole lifecycle at 375 px.
*/
import { appReady, defaultRun, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";

test("Listen plays the recorded run's own report per edition, with captions", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  const run = await defaultRun(page);
  test.skip(run.provenance !== "pipeline", "the site has no recorded run with audio");
  await page.goto(`/#/recap/${run.run_id}`);
  await expect(page.getByRole("heading", { name: "Listen to the English report" })).toBeVisible();
  const audio = await page.request.get(`/runs/${run.run_id}/audio-en.mp3`);
  expect(audio.ok()).toBe(true);
  expect(audio.headers()["content-type"]).toContain("audio/mpeg");
  await page.getByRole("tab", { name: /^EL/ }).click();
  await expect(page.getByRole("heading", { name: "Listen to the Greek report" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Captions" })).toBeVisible();
  await expect(page.getByText(/The Greek goal line:/)).toBeVisible();
  await expect(page.locator('audio[src$="audio-el.mp3"]')).toHaveCount(1);
  await expectNoSeriousAxe(page, "listen 1280");
  await page.getByRole("heading", { name: "Listen to the Greek report" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "screenshots/40-listen-greek-1280-light.png" });
});

test("Pitch Sound starts only on a tap and can be muted", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const start = page.getByRole("button", { name: "Pitch Sound" });
  await expect(start).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Mute", exact: true })).toHaveCount(0);
  await start.click();
  await expect(start).toHaveAttribute("aria-pressed", "true");
  const mute = page.getByRole("button", { name: "Mute", exact: true });
  await mute.click();
  await expect(mute).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await start.click();
  await expect(start).toHaveAttribute("aria-pressed", "false");
});

test("the Newsroom Wall stepper shows the whole lifecycle on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  const run = await defaultRun(page);
  await page.goto(`/#/newsroom/${run.run_id}`);
  const states = page.getByRole("list", { name: "Bundle states" });
  for (const s of run.states) {
    const words = { DRAFT: "Draft", CHECKED: "Checked", SEALED: "Sealed", AWAITING_APPROVAL: "Awaiting approval", APPROVED: "Approved", PUBLISHED: "Published", CHANGES_REQUESTED: "Changes requested" }[s.state];
    await expect(states.getByText(new RegExp(`^${words}`))).toBeVisible();
  }
  await expectNoHorizontalScroll(page);
  await expectNoSeriousAxe(page, "wall stepper 375");
  await states.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "screenshots/41-wall-lifecycle-375-light.png" });
});
