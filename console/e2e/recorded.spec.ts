/*
  The site once a recorded pipeline run is listed first. Runs only in the
  recorded project (playwright.config.ts), against the rehearsal build in
  dist-recorded: web/runs plus the recorded-run fixture that
  python -m agon.webrecord exported from a bundle the real seal, verifier and
  Publisher produced (tests/recorded_kit.py).

  Every screen that shows the run states its label in place, the recap states
  the approved label, Verify recomputes the bundle hash and every published
  file and reports the two unticked destination payloads as withheld by design,
  and axe finds nothing serious. Screenshots go to screenshots/20-recorded-*.
*/
import { appReady, defaultRun, expect, expectNoHorizontalScroll, expectNoSeriousAxe, test } from "./fixtures";
import { editionLabel } from "../src/lib/runs";

const VIEWPORTS = [
  { width: 375, height: 812 },
  { width: 1280, height: 800 },
];

test("the recorded run is the default, and the served page states its labels", async ({ page }) => {
  const response = await page.goto("/");
  const body = (await response?.text()) ?? "";
  const run = await defaultRun(page);
  expect(run.provenance).toBe("pipeline");
  expect(run.approval.approved_bundle_sha256).toBe(run.bundle.sha256);
  expect(body).toContain(`id="wall-label">${run.label}`);
  expect(body).toContain(`id="approve-label">${run.label}`);
  expect(body).toContain(`id="recap-label">${editionLabel(run)}`);
  expect(editionLabel(run)).toMatch(/^AI-drafted\. Numbers and names checked by code\. Headline report and flagged sentences approved by the club media office at /);
});

for (const viewport of VIEWPORTS) {
  test(`recorded run screens at ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
    await page.goto("/");
    await appReady(page);
    const run = await defaultRun(page);
    const id = run.run_id;

    await page.goto(`/#/newsroom/${id}`);
    await expect(page.locator("#wall-label")).toHaveText(run.label);
    await expectNoHorizontalScroll(page);
    await expectNoSeriousAxe(page, `recorded wall ${viewport.width}`);
    await page.screenshot({ path: `screenshots/20-recorded-newsroom-wall-${viewport.width}-light.png`, fullPage: true });

    await page.goto(`/#/approve/${id}`);
    await expect(page.locator("#approve-label")).toHaveText(run.label);
    await expect(page.getByText("These destinations are read from the run file", { exact: false })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expectNoSeriousAxe(page, `recorded card ${viewport.width}`);
    await page.screenshot({ path: `screenshots/21-recorded-approval-card-${viewport.width}-light.png`, fullPage: true });

    await page.goto(`/#/recap/${id}`);
    await expect(page.locator("#recap-label")).toHaveText(editionLabel(run));
    await expectNoHorizontalScroll(page);
    await expectNoSeriousAxe(page, `recorded recap ${viewport.width}`);
    await page.screenshot({ path: `screenshots/22-recorded-recap-${viewport.width}-light.png`, fullPage: true });

    await page.goto("/#/verify");
    await expect(page.locator('[data-view="verify"] section[data-state="done"]').first()).toBeVisible({ timeout: 30_000 });
    const short = run.bundle.sha256.slice(0, 12);
    await expect(
      page.getByText(
        new RegExp(
          `the bundle hash equals the hash on the approval card, ${short}, and (\\d+) of \\1 published files match the sealed manifest, and 2 unticked destination payloads are withheld by design\\.`,
        ),
      ),
    ).toBeVisible({ timeout: 30_000 });
    /* One block per listed run, each under its own label; the export on top holds the two
       withheld payloads, and every approved pipeline run finds its approved hash equal. */
    const index = await (await page.request.get("/runs/index.json")).json();
    const blocks = page.locator('[data-view="verify"] section[data-run-id]');
    await expect(blocks).toHaveCount(index.runs.length);
    const block = page.locator(`[data-view="verify"] section[data-run-id="${id}"]`);
    await expect(block.getByRole("heading", { name: run.label })).toBeVisible();
    await expect(block.getByText("Withheld by design", { exact: true })).toHaveCount(2);
    for (const row of index.runs as { run_id: string; provenance: string }[]) {
      const comparison = page.locator(`[data-view="verify"] section[data-run-id="${row.run_id}"]`);
      const expected = row.provenance === "pipeline" ? /The approved hash equals the published hash/ : /There is no approved hash to compare/;
      await expect(comparison.getByText(expected)).toBeVisible();
    }
    await expectNoHorizontalScroll(page);
    await expectNoSeriousAxe(page, `recorded verify ${viewport.width}`);
    await page.screenshot({ path: `screenshots/23-recorded-verify-${viewport.width}-light.png`, fullPage: true });
  });
}

test("a committed cue file reaches the replay as a lower-third, labelled with its charter hash", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await appReady(page);
  await expect(page.locator('[data-view="home"] section[data-analysis="ready"]')).toBeAttached({ timeout: 30_000 });
  await page.getByLabel("Match time").focus();
  await page.keyboard.press("End");
  await expect(page.getByText(/Rehearsal cue: this lower-third comes from a committed test file/)).toBeVisible();
  await expect(page.getByText("Live cue, charter 000000000000")).toBeVisible();
  await page.screenshot({ path: "screenshots/24-recorded-cue-lower-third-1280-light.png" });
});
