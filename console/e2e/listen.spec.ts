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
  await page.locator('section[aria-labelledby="h-listen"]').screenshot({ path: "screenshots/40-listen-greek-1280-light.png" });
});

test("the narration pauses when the visitor leaves the recap", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await appReady(page);
  const run = await defaultRun(page);
  test.skip(run.provenance !== "pipeline", "the site has no recorded run with audio");
  await page.goto(`/#/recap/${run.run_id}`);
  const audio = page.locator('section[aria-labelledby="h-listen"] audio');
  const started = await audio.evaluate(async (a: HTMLAudioElement) => {
    a.muted = true;
    try {
      await a.play();
      return true;
    } catch {
      return false;
    }
  });
  expect(started, "the recorded mp3 plays in the browser").toBe(true);
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.paused)).toBe(false);
  await page.getByRole("link", { name: /Verify/ }).first().click();
  await expect(page).toHaveURL(/#\/verify/);
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.paused)).toBe(true);
});

/* Counts every AudioContext the page creates, so "only on a tap" is checked, not assumed. */
const COUNT_AUDIO_CONTEXTS = () => {
  const Original = window.AudioContext;
  const w = window as unknown as { __agonAudioContexts: number };
  w.__agonAudioContexts = 0;
  window.AudioContext = class extends Original {
    constructor(options?: AudioContextOptions) {
      super(options);
      w.__agonAudioContexts += 1;
    }
  };
};

test("Pitch Sound starts only on a tap and can be muted", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.addInitScript(COUNT_AUDIO_CONTEXTS);
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const start = page.getByRole("button", { name: "Pitch Sound" });
  await expect(start).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("button", { name: "Mute", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(800);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { __agonAudioContexts: number }).__agonAudioContexts), "no audio before the tap").toBe(0);
  await start.click();
  await expect(start).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => (window as unknown as { __agonAudioContexts: number }).__agonAudioContexts)).toBe(1);
  const mute = page.getByRole("button", { name: "Mute", exact: true });
  await mute.click();
  await expect(mute).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await start.click();
  await expect(start).toHaveAttribute("aria-pressed", "false");
});

test("the tick stops while another seed loads, and Pitch Sound stops when the visitor leaves", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  /* Seed 2's event file never arrives, so the replay stays loading, as a slow network would leave it. */
  await page.route("**/data/2.ndjson", () => undefined);
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const start = page.getByRole("button", { name: "Pitch Sound" });
  await start.click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(start).toHaveAttribute("data-ticking", "true");
  await page.getByText("Seed 2", { exact: true }).click();
  await expect(page.locator('[data-view="home"] section[data-state="loading"]')).toBeVisible();
  await expect(start).toHaveAttribute("data-ticking", "false");
  await expect(start).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("link", { name: /Verify/ }).first().click();
  await expect(page).toHaveURL(/#\/verify/);
  /* The Match Centre is hidden now, so the button is found by its attribute, not its role. */
  const hidden = page.locator('[data-view="home"] button[data-ticking]');
  await expect(hidden).toHaveAttribute("aria-pressed", "false");
  await expect(hidden).toHaveAttribute("data-ticking", "false");
});

test("a hidden tab stops Pitch Sound, since the replay freezes there", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const start = page.getByRole("button", { name: "Pitch Sound" });
  await start.click();
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await expect(start).toHaveAttribute("data-ticking", "true");
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(start).toHaveAttribute("aria-pressed", "false");
  await expect(start).toHaveAttribute("data-ticking", "false");
});

test("the players appear only when asked for, with name tags from the synthetic tracking", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/kinematics/")) {
      asked.push(r.url());
    }
  });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  expect(asked, "no tracking file is fetched on first paint").toEqual([]);
  await page.getByLabel("Match time").fill("1500");
  await page.getByRole("button", { name: "Show the players" }).click();
  await expect(page.getByText(/Synthetic tracking, modelled from the event log within speed and acceleration caps, not measured/)).toBeVisible({ timeout: 30_000 });
  expect(asked.some((u) => /kinematics\/1\.kinematics\.json$/.test(u))).toBe(true);
  const pitch = page.locator('[data-view="home"] figure').first();
  await expect.poll(() => pitch.locator("circle[data-player]").count()).toBeGreaterThanOrEqual(20);
  await expect(pitch.locator("svg text").first()).toBeAttached();
  await expect(page.getByRole("link", { name: "kinematics/README.txt" })).toBeVisible();
  await expect(page.getByText(/Positions are modelled and can miss the event spot/)).toBeVisible();
  await expectNoSeriousAxe(page, "players on the pitch 1280");
  await pitch.screenshot({ path: "screenshots/42-players-on-pitch-1280-light.png" });
});

test("without the match file the players control says why and stays off", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  /* A web page where the event file should be, as a misrouted host would answer: the match cannot be read. */
  await page.route("**/data/1.ndjson", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>Not the file</title>" }));
  await page.goto("/");
  await appReady(page);
  const show = page.getByRole("button", { name: "Show the players" });
  await expect(show).toBeDisabled({ timeout: 30_000 });
  await expect(page.getByText("The synthetic tracking needs the match file, which could not be read, so the players cannot be shown.")).toBeVisible();
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
