/*
  Your view: the same match as two different experiences, and the viewer's
  own choices. Fan mode tells the story in words; Analyst mode shows every
  chart and scorecard; a favourite club and player, following that player,
  and one metric reshape the page, and the choices survive a reload (stored
  per browser). Each state is checked with axe and screenshotted.
*/
import type { Page } from "@playwright/test";
import { appReady, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";

async function openAtFullTime(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const slider = page.getByLabel("Match time");
  await slider.focus();
  await page.keyboard.press("End");
}

test("fan mode tells the story in words, with the scoreboard, timeline and star player", async ({ page }) => {
  await openAtFullTime(page, 375, 812);
  await expect(page.getByRole("heading", { name: "The match, told for a fan" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "The story so far" })).toBeVisible();
  await expect(page.getByText(/Goal for Pellmoor Harriers \(home\): Arno Quenrick scores\./)).toBeVisible();
  await page.getByRole("button", { name: /Show all \d+ moments so far/ }).click();
  await expect(page.getByText(/Goal for Zennmark Town \(away\): Liro Ellow scores\./)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Star player" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Territory" })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
  await expectNoSeriousAxe(page, "fan mode 375");
  await page.screenshot({ path: "screenshots/30-fan-mode-375-light.png", fullPage: true });
});

test("analyst mode shows every chart and scorecard", async ({ page }) => {
  await openAtFullTime(page, 1280, 800);
  await page.getByText("Analyst", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "The match in numbers" })).toBeVisible();
  for (const title of ["Win probability", "xG race", "Momentum, pressure and control", "Shot map", "Territory", "Team stats", "Player scorecards"]) {
    await expect(page.getByRole("heading", { name: title, exact: true })).toBeVisible();
  }
  await expectNoSeriousAxe(page, "analyst mode 1280");
  await page.screenshot({ path: "screenshots/31-analyst-mode-1280-light.png", fullPage: true });
});

test("a favourite club and a followed player reshape the page and survive a reload", async ({ page }) => {
  await openAtFullTime(page, 1280, 800);
  await page.getByLabel("Favourite club").selectOption({ label: "Pellmoor Harriers" });
  await page.getByLabel("Favourite player").selectOption({ label: "16 Liro Radrick" });
  await page.getByLabel("Follow this player through the match").check();
  await expect(page.getByRole("heading", { name: /Your club: Pellmoor Harriers/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Following Liro Radrick/ })).toBeVisible();
  await expect(page.getByText("Mean pass difficulty")).toBeVisible();
  await expectNoSeriousAxe(page, "follow a player 1280");
  await page.screenshot({ path: "screenshots/32-follow-player-1280-light.png", fullPage: true });

  await page.reload();
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: /Following Liro Radrick/ })).toBeVisible();
  await expect(page.getByLabel("Favourite club")).toHaveValue(/C[0-9]+/);
});

test("one metric tells the whole match through that number", async ({ page }) => {
  await openAtFullTime(page, 1280, 800);
  await page.getByLabel("One metric I care about").selectOption({ label: "Win probability" });
  await expect(page.getByText(/The whole match told through one number/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Win probability", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Player scorecards" })).toHaveCount(0);
  await expectNoSeriousAxe(page, "one metric 1280");
  await page.screenshot({ path: "screenshots/33-one-metric-1280-light.png", fullPage: true });
});

test("the scoreboard and timeline say whose phase it is in words, not colour alone", async ({ page }) => {
  await openAtFullTime(page, 1280, 800);
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  const replay = page.locator('[data-view="home"] section[data-state="ready"]');
  await expect(replay.getByText("Home", { exact: true }).first()).toBeVisible();
  await expect(replay.getByText("Away", { exact: true }).first()).toBeVisible();
  await expect(replay.getByText(/above the line/)).toBeVisible();
  await expectNoSeriousAxe(page, "replay dark 1280");
  await page.screenshot({ path: "screenshots/34-replay-dark-1280.png" });
});

/* Before a charter is approved the page says so. Once web/cues/1.json is exported, the replay is
   moved to the first listed cue's window (real cues last a few seconds around their trigger) and
   its lower-third must show there, labelled with the charter hash. */
test("timed cues show inside a listed cue's window, or the page says none is approved", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  const list = await (await page.request.get("/cues/manifest.json")).json();
  const entry = (list.files as { seed: number; name: string }[]).find((f) => f.seed === 1);
  if (!entry) {
    await expect(page.getByText(/Timed cues: there is no approved charter and no cue file for seed 1 yet/)).toBeVisible();
    return;
  }
  const cues = (await (await page.request.get(`/cues/${entry.name}`)).json()).cues as { at_s: number; until_s: number }[];
  expect(cues.length, `cues/${entry.name} is listed for seed 1 but holds no cue`).toBeGreaterThan(0);
  const axis = (await (await page.request.get("/match/1.json")).json()).axis as { half_offset_s: number; half_time_s: number };
  const events = (await (await page.request.get("/data/1.ndjson")).text())
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { period: number; minute: number; second: number });
  /* The drawn axis of the match analysis, and the replay clock of lib/core.ts buildTimeline (the
     second half follows the first half's last event). */
  const clock = (e: { minute: number; second: number }) => e.minute * 60 + e.second;
  const p1End = Math.max(...events.filter((e) => e.period === 1).map(clock)) + 1;
  const onAxis = (e: { period: number; minute: number; second: number }) => clock(e) + (e.period === 2 ? axis.half_offset_s : 0);
  const onReplay = (e: { period: number; minute: number; second: number }) => (e.period === 1 ? clock(e) : p1End + clock(e) - 2700);
  /* The first cue whose window holds an event: the replay can stand on that event, so the cue applies. */
  const pick = cues.map((c) => ({ c, e: events.find((e) => c.at_s <= onAxis(e) && onAxis(e) < c.until_s) })).find((x) => x.e);
  expect(pick, "no listed cue's window holds an event of the replay, so none can be shown").toBeTruthy();
  const label = page.getByText(/Timed cue, charter [0-9a-f]{12}/);
  await page.getByLabel("Match time").fill(String(onReplay(pick!.e!)));
  await expect(label).toBeVisible();
});
