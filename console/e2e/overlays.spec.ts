/*
  The parts that render on demand, where a CSP problem would hide: the Radix
  dialog, sheet, popover and tooltip. Each is opened, checked with axe while
  open, screenshotted, and closed with focus back on its button. The guard in
  fixtures.ts fails the test on any CSP violation, including an injected
  <style> element.

  Also the first-run guide (skip remembered per browser) and the theme switch.
*/
import { readFileSync } from "node:fs";
import { appReady, defaultRun, expect, expectNoSeriousAxe, test } from "./fixtures";
import { editionLabel } from "../src/lib/runs";

test("glossary popover opens on click, explains the term and returns focus", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await appReady(page);
  const term = page.getByRole("button", { name: /^xG/ });
  await term.click();
  const popover = page.getByRole("dialog", { name: /xG/ });
  await expect(popover).toBeVisible();
  await expect(popover).toContainText("Expected goals");
  await expectNoSeriousAxe(page, "glossary popover");
  await page.screenshot({ path: "screenshots/10-glossary-popover-375-light.png" });
  await page.keyboard.press("Escape");
  await expect(popover).toBeHidden();
  await expect(term).toBeFocused();
});

test("menu sheet on a phone lists the judge path and returns focus", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await appReady(page);
  const menu = page.getByRole("button", { name: "Open the menu" });
  await menu.click();
  const sheet = page.getByRole("dialog", { name: "Menu" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("link", { name: /Verify/ })).toBeVisible();
  await expect(page.locator("html")).toHaveClass(/scroll-locked/);
  await expectNoSeriousAxe(page, "menu sheet");
  await page.screenshot({ path: "screenshots/11-menu-sheet-375-light.png" });
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(menu).toBeFocused();
  await expect(page.locator("html")).not.toHaveClass(/scroll-locked/);
});

test("Why drawer opens from a deep link and from a sentence", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#/recap/sample-s1?s=en-05");
  await appReady(page);
  const drawer = page.getByRole("dialog", { name: "Why this sentence says this" });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText("Injury or medical");
  await expect(drawer).toContainText("Headline report and a Confirm row");
  await expectNoSeriousAxe(page, "why drawer");
  await page.screenshot({ path: "screenshots/12-why-drawer-1280-light.png" });
  await page.getByRole("button", { name: "Close the Why drawer" }).click();
  await expect(drawer).toBeHidden();
});

test("How it works opens each node's evidence in a sheet on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/#/how-it-works");
  await appReady(page);
  const node = page.getByRole("button", { name: /Fact Gate/ });
  await node.click();
  const sheet = page.getByRole("dialog", { name: "Fact Gate" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("link", { name: /src\/agon\/governance\/factgate\.py:276/ })).toBeVisible();
  await expectNoSeriousAxe(page, "evidence sheet");
  await page.screenshot({ path: "screenshots/13-evidence-sheet-375-dark.png" });
  await page.keyboard.press("Escape");
  await expect(node).toBeFocused();
});

test("tooltip on the theme switch, and the theme choice persists", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/#/about");
  await appReady(page);
  const toggle = page.getByRole("button", { name: "Switch to dark theme" });
  await toggle.hover();
  await expect(page.getByRole("tooltip")).toContainText("Switch to dark theme");
  await page.screenshot({ path: "screenshots/14-tooltip-1280-light.png" });
  await toggle.click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await appReady(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible();
});

test("the first-run guide can be skipped, stays skipped, and comes back from the footer", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await appReady(page);
  const guide = page.getByRole("heading", { name: /New here\? Three steps/ });
  await expect(guide).toBeVisible();
  await page.getByRole("button", { name: "Skip the guide" }).click();
  await expect(guide).toBeHidden();
  await page.reload();
  await appReady(page);
  await expect(guide).toBeHidden();
  await page.getByRole("button", { name: "Show the guide again" }).click();
  await expect(guide).toBeVisible();
});

test("the served page carries the strict CSP and every readiness marker", async ({ page }) => {
  const response = await page.goto("/");
  expect(response).not.toBeNull();
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("script-src 'self';");
  expect(csp).toContain("style-src 'self';");
  expect(csp).toContain("require-trusted-types-for 'script'");
  const body = (await response?.text()) ?? "";
  /* Every marker of the demo target, read from ci/readiness.json, which names the labels of
     the run the site shows by default (and the commit, added at deploy, is stamped here too). */
  const config = JSON.parse(readFileSync(new URL("../../ci/readiness.json", import.meta.url), "utf8")) as {
    targets: { name: string; markers: string[] }[];
  };
  const markers = config.targets.find((t) => t.name === "demo")?.markers ?? [];
  expect(markers.filter((m) => /^id="(wall|approve|recap)-label">/.test(m)).length).toBe(3);
  expect(markers.filter((m) => !body.includes(m))).toEqual([]);
  const shown = await defaultRun(page);
  expect(body).toContain(`id="wall-label">${shown.label}`);
  expect(body).toContain(`id="recap-label">${editionLabel(shown)}`);
});

test("the Data Lab shows the season table and the self-measured bands, each file hashed in the browser", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/#/data-lab");
  await appReady(page);
  await expect(page.getByText(/Every season file below matched its sha256 in season\/manifest\.json/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("heading", { name: /Final table/ })).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByText(/bands pass on seeds/)).toBeVisible();
  await expect(page.getByText("self-measured", { exact: true })).toBeVisible();
});
