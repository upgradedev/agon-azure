/*
  Every test fails on any console error, any uncaught page error and any
  Content-Security-Policy violation (the securitypolicyviolation event, which
  also catches a blocked inline style or a Trusted Types refusal).
*/
import { test as base, expect, type Locator, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

declare global {
  interface Window {
    __agonViolations?: string[];
  }
}

export const LABEL = "Design preview: sample run written by hand, not produced by the pipeline.";
export const STANDIN = "Stand-in for the Microsoft Teams card. It calls the same decision endpoint. The real Teams card is in the video.";

export const test = base.extend<{ guard: void }>({
  guard: [
    async ({ page }, use) => {
      const problems: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") {
          problems.push(`console error: ${message.text()}`);
        }
      });
      page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
      await page.addInitScript(() => {
        window.__agonViolations = [];
        document.addEventListener("securitypolicyviolation", (event) => {
          window.__agonViolations?.push(`${event.violatedDirective} blocked ${event.blockedURI || "inline"}: ${event.sample}`);
        });
      });
      await use();
      const violations = await page.evaluate(() => window.__agonViolations ?? []).catch(() => [] as string[]);
      expect([...problems, ...violations.map((v) => `CSP violation: ${v}`)]).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

/* The app has hydrated the prerendered page. */
export async function appReady(page: Page): Promise<void> {
  await page.locator('html[data-app="ready"]').waitFor({ state: "attached" });
}

/* axe-core with the WCAG 2.x A and AA rules; serious and critical fail. */
export async function expectNoSeriousAxe(page: Page, where: string): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${where}: ${v.id} (${v.impact}) on ${v.nodes.length} nodes, e.g. ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious).toEqual([]);
}

export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "horizontal scroll").toBeLessThanOrEqual(0);
}

/* Presses Tab until the target has focus, so a path is proven keyboard-only. */
export async function tabTo(page: Page, target: Locator, max = 120): Promise<void> {
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((node) => node === document.activeElement).catch(() => false)) {
      return;
    }
  }
  throw new Error(`not reachable with Tab within ${max} presses`);
}
