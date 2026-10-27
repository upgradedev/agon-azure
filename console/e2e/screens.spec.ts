/*
  Every screen at 375 and 1280 px, in light and dark: no horizontal scroll at
  375, axe with zero serious or critical violations, and a full-page
  screenshot into screenshots/ (uploaded as a CI artifact on every run), plus
  the first screen at 375 px as a phone shows it. In the full-page shots the
  phone's sticky Blow full time bar appears where the first screen ended.
  Reduced motion keeps the screenshots still.
*/
import { appReady, defaultRun, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";
import type { Page } from "@playwright/test";
import { THEME_WORDS } from "../src/lib/runs";
import type { TurningPointTheme } from "../src/lib/types";

const SCREENS: { name: string; hash: string; view: string }[] = [
  { name: "01-match-centre", hash: "#/", view: "home" },
  { name: "02-newsroom-wall", hash: "#/newsroom", view: "newsroom" },
  { name: "03-approval-card", hash: "#/approve", view: "approve" },
  { name: "04-recap", hash: "#/recap", view: "recap" },
  { name: "05-verify", hash: "#/verify", view: "verify" },
  { name: "06-how-it-works", hash: "#/how-it-works", view: "how-it-works" },
  { name: "07-data-lab", hash: "#/data-lab", view: "data-lab" },
  { name: "08-about", hash: "#/about", view: "about" },
  { name: "09-media-office", hash: "#/media-office", view: "media-office" },
];

const VIEWPORTS = [
  { width: 375, height: 812 },
  { width: 1280, height: 800 },
];

async function settled(page: Page, view: string): Promise<void> {
  if (view === "home") {
    await expect(page.locator('[data-view="home"] section[data-state="ready"]')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 20_000 });
  }
  if (view === "data-lab") {
    await expect(page.locator('[data-view="data-lab"] [data-state="ready"]')).toBeVisible({ timeout: 30_000 });
  }
  if (view === "media-office") {
    await expect(page.locator('[data-view="media-office"] [data-ops]').first()).toBeAttached({ timeout: 30_000 });
  }
  if (view === "verify") {
    await expect(page.locator('[data-view="verify"] section[data-state="done"]').first()).toBeVisible({ timeout: 30_000 });
  }
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    for (const screen of SCREENS) {
      test(`${screen.name} at ${viewport.width} px, ${theme}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
        await page.goto(`/${screen.hash}`);
        await appReady(page);
        await expect(page.locator(`[data-view="${screen.view}"]`)).toBeVisible();
        await settled(page, screen.view);
        await expectNoHorizontalScroll(page);
        await expectNoSeriousAxe(page, `${screen.name} ${viewport.width} ${theme}`);
        if (viewport.width === 375) {
          /* What a phone shows before any scrolling, sticky bar included. */
          await page.screenshot({ path: `screenshots/${screen.name}-${viewport.width}-${theme}-fold.png` });
        }
        await page.screenshot({ path: `screenshots/${screen.name}-${viewport.width}-${theme}.png`, fullPage: true });
      });
    }
  }
}

/* The Analyst desk on the run the site shows by default: every turning point its sealed
   card.json carries, read from the served file, with the minute, the theme and the agent's why. */
type SealedPick = { author: string; event_id: string; minute: string; theme: TurningPointTheme; why: string };

async function sealedPicks(page: Page, dir: string): Promise<SealedPick[]> {
  const response = await page.request.get(`/runs/${dir}/card.json`);
  expect(response.ok(), "the sealed card is served").toBe(true);
  return ((await response.json()) as { turning_points: SealedPick[] }).turning_points;
}

for (const viewport of VIEWPORTS) {
  test(`the Analyst desk shows the default run's sealed turning points at ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/#/newsroom");
    await appReady(page);
    const run = await defaultRun(page);
    const sealed = await sealedPicks(page, run.bundle.dir);
    expect(sealed.length, "the default run's card carries turning points").toBeGreaterThan(0);
    expect(sealed.every((p) => p.author === "model"), "the agent wrote every pick").toBe(true);
    const desk = page.locator('[data-view="newsroom"] section[data-desk="analyst"]');
    await expect(desk).toBeVisible();
    await expect(desk.getByRole("heading", { name: "Analyst desk: turning points" })).toBeVisible();
    await expect(desk.getByText("AI-written", { exact: true })).toBeVisible();
    const items = desk.locator("li[data-pick]");
    await expect(items).toHaveCount(sealed.length);
    for (const [i, pick] of sealed.entries()) {
      const item = items.nth(i);
      await expect(item).toHaveAttribute("data-pick", pick.event_id);
      await expect(item).toContainText(`Minute ${pick.minute}`);
      await expect(item).toContainText(THEME_WORDS[pick.theme]);
      await expect(item).toContainText(pick.why);
    }
    await expect(desk.getByText(/^Written by the Microsoft Foundry agent agon-analyst:\d+\./)).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
}

test("the Why drawer names the turning point a sentence cites", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/#/recap");
  await appReady(page);
  const run = await defaultRun(page);
  const sealed = await sealedPicks(page, run.bundle.dir);
  const file = run.editions.find((e) => e.edition === "en")?.file ?? "";
  const report = (await (await page.request.get(`/runs/${run.bundle.dir}/${file}`)).json()) as {
    sentences: { claims: { event_ids?: string[] }[] }[];
  };
  const cites = (index: number): SealedPick[] =>
    sealed.filter((p) => report.sentences[index].claims.some((c) => (c.event_ids ?? []).includes(p.event_id)));
  const index = report.sentences.findIndex((_s, i) => cites(i).length > 0);
  expect(index, "a headline sentence cites a turning point").toBeGreaterThanOrEqual(0);
  await page.getByRole("tab", { name: /^EN/ }).click();
  await page.locator('[data-view="recap"] p[lang="en"] a').nth(index).click();
  const drawer = page.getByRole("dialog", { name: "Why this sentence says this" });
  await expect(drawer).toBeVisible();
  const rows = drawer.locator("li[data-pick]");
  await expect(rows).toHaveCount(cites(index).length);
  await expect(rows.first()).toContainText(`Minute ${cites(index)[0].minute}`);
  await expect(rows.first()).toContainText(cites(index)[0].why);
  await expect(rows.first()).toContainText("AI-written by the Analyst agent");
});
