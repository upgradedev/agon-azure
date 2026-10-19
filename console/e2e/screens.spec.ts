/*
  Every screen at 375 and 1280 px, in light and dark: no horizontal scroll at
  375, axe with zero serious or critical violations, and a full-page
  screenshot into screenshots/ (uploaded as a CI artifact on every run).
  Reduced motion keeps the screenshots still.
*/
import { appReady, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";
import type { Page } from "@playwright/test";

const SCREENS: { name: string; hash: string; view: string }[] = [
  { name: "01-match-centre", hash: "#/", view: "home" },
  { name: "02-newsroom-wall", hash: "#/newsroom", view: "newsroom" },
  { name: "03-approval-card", hash: "#/approve", view: "approve" },
  { name: "04-recap", hash: "#/recap", view: "recap" },
  { name: "05-verify", hash: "#/verify", view: "verify" },
  { name: "06-how-it-works", hash: "#/how-it-works", view: "how-it-works" },
  { name: "07-data-lab", hash: "#/data-lab", view: "data-lab" },
  { name: "08-about", hash: "#/about", view: "about" },
];

const VIEWPORTS = [
  { width: 375, height: 812 },
  { width: 1280, height: 800 },
];

async function settled(page: Page, view: string): Promise<void> {
  if (view === "home") {
    await expect(page.locator('[data-view="home"] section[data-state="ready"]')).toBeVisible({ timeout: 20_000 });
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
        await page.screenshot({ path: `screenshots/${screen.name}-${viewport.width}-${theme}.png`, fullPage: true });
      });
    }
  }
}
