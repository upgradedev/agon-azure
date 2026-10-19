/*
  Submission video capture, CI only. One clip per web part of video/beats.json,
  recorded from the LIVE site at a stated release SHA, never from a local build.

  Not a console test: the file has no .spec suffix, so the default
  playwright.config.ts never runs it; playwright.video.config.ts does, from
  .github/workflows/video.yml (main only) and video-check.yml (the pull request
  rehearsal). Nothing here runs on a developer machine.

  Per clip:
  1. A fresh context at the beat's framing (1920 x 1080, or 375 x 812 at device
     scale 2 for a phone beat), dark theme, first-run guide skipped, recording.
  2. Before anything is used, the page's body data-commit must equal
     AGON_VIDEO_RELEASE_SHA and runs/index.json must list the run the sheet names.
  3. The lead mark: compose.py trims everything before it.
  4. The beat's script acts on stable hooks (data-view, data-run-id, roles).
  5. Every fact the beat lists must be visible on the page: the narration says
     it, so a false sentence fails here instead of reaching a judge.
  6. The page is held until max_seconds plus a margin after the lead, so the
     clip covers whatever length the narration measures.
  A receipt <beat>.json beside <beat>.webm records the release, the commit the
  page served, the lead, the hold and the facts checked.

  Environment: AGON_VIDEO_BEATS (path to beats.json), AGON_VIDEO_RELEASE_SHA,
  AGON_VIDEO_CLIPS (output folder), optional AGON_VIDEO_ONLY (comma-separated
  beat ids, default all), AGON_VIDEO_RUN_ID and AGON_VIDEO_SITE (overrides).
*/
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Locator, type Page } from "@playwright/test";

interface Fact {
  text: string;
  within?: string;
}

interface Part {
  source: string;
  viewport?: "desktop" | "phone";
  seconds?: number;
  actions?: string;
  facts?: Fact[];
}

interface Beat {
  id: string;
  narration: string;
  visual: string | Part | (string | Part)[];
  min_seconds: number;
  max_seconds: number;
}

interface Sheet {
  site: string;
  run_id: string;
  theme: "dark" | "light";
  beats: Beat[];
}

const FRAMING = {
  desktop: { width: 1920, height: 1080, scale: 1 },
  phone: { width: 375, height: 812, scale: 2 },
} as const;
const MARGIN_SECONDS = 1.5;
const SETTLE_MS = 800;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set; the video workflows set it`);
  }
  return value;
}

const sheet = JSON.parse(readFileSync(required("AGON_VIDEO_BEATS"), "utf-8")) as Sheet;
const releaseSha = required("AGON_VIDEO_RELEASE_SHA");
const clipsDir = required("AGON_VIDEO_CLIPS");
const site = (process.env.AGON_VIDEO_SITE || sheet.site).replace(/\/+$/, "");
const runId = process.env.AGON_VIDEO_RUN_ID || sheet.run_id;
const only = (process.env.AGON_VIDEO_ONLY || "all")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

if (!/^[0-9a-f]{40}$/.test(releaseSha)) {
  throw new Error(`AGON_VIDEO_RELEASE_SHA must be 40 hex characters, got ${releaseSha}`);
}

function parts(beat: Beat): Part[] {
  const list = Array.isArray(beat.visual) ? beat.visual : [beat.visual];
  return list.map((part) => (typeof part === "string" ? { source: part } : part));
}

function webPart(beat: Beat): Part | undefined {
  return parts(beat).find((part) => part.source.startsWith("web:"));
}

function fill(text: string): string {
  return text.replaceAll("{run_id}", runId);
}

/* Change the hash route in place, as a click on a nav link would. */
async function go(page: Page, route: string): Promise<void> {
  await page.evaluate((hash) => {
    window.location.hash = hash;
  }, fill(route));
}

/* Bring an element to the middle of the screen with a visible scroll. */
async function glide(page: Page, target: Locator): Promise<void> {
  await target.first().evaluate((element) => element.scrollIntoView({ behavior: "smooth", block: "center" }));
  await page.waitForTimeout(1200);
}

type Script = (page: Page) => Promise<void>;

/* One script per beat id; each acts on hooks the console's own tests rely on. */
const SCRIPTS: Record<string, Script> = {
  verify: async (page) => {
    await go(page, "#/verify");
    const view = page.locator('[data-view="verify"]');
    await expect(view).toBeVisible();
    await expect(view.locator('[aria-labelledby="h-bundle"]')).toHaveAttribute("data-state", "done", { timeout: 30_000 });
    await page.waitForTimeout(1500);
    await glide(page, view.locator(`section[data-run-id="${runId}"]`).getByText(/Approved hash and published hash/));
  },
};

const known = new Set(sheet.beats.map((beat) => beat.id));
const unknown = only.filter((name) => name !== "all" && !known.has(name));
if (unknown.length) {
  throw new Error(`AGON_VIDEO_ONLY names beats the sheet does not have: ${unknown.join(", ")}`);
}
const selected = sheet.beats.filter((beat) => webPart(beat) && (only.includes("all") || only.includes(beat.id)));

test.describe.configure({ mode: "serial" });

for (const beat of selected) {
  test(`capture ${beat.id}`, async ({ browser }) => {
    const part = webPart(beat) as Part;
    const script = SCRIPTS[beat.id];
    expect(script, `a capture script exists for web beat ${beat.id}`).toBeDefined();
    const hold = beat.max_seconds + MARGIN_SECONDS;
    test.setTimeout((hold + 120) * 1000);
    const framing = FRAMING[part.viewport ?? "desktop"];
    mkdirSync(join(clipsDir, "raw"), { recursive: true });

    const context = await browser.newContext({
      viewport: { width: framing.width, height: framing.height },
      deviceScaleFactor: framing.scale,
      colorScheme: sheet.theme,
      reducedMotion: "no-preference",
      recordVideo: {
        dir: join(clipsDir, "raw"),
        size: { width: framing.width * framing.scale, height: framing.height * framing.scale },
      },
    });
    await context.addInitScript((theme: string) => {
      try {
        window.localStorage.setItem("agon-guide", "skipped");
        window.localStorage.setItem("agon-theme", theme);
      } catch {
        /* No storage: the defaults apply. */
      }
    }, sheet.theme);
    const page = await context.newPage();
    const opened = Date.now();
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${site}/`, { waitUntil: "load" });
    const served = await page.locator("body").getAttribute("data-commit");
    expect(served, `the live site serves the release SHA ${releaseSha}`).toBe(releaseSha);
    await page.locator('html[data-app="ready"]').waitFor({ state: "attached", timeout: 30_000 });
    const index = (await (await page.request.get(`${site}/runs/index.json`)).json()) as { runs: { run_id: string }[] };
    expect(
      index.runs.map((row) => row.run_id),
      `the live site lists run ${runId}`,
    ).toContain(runId);
    await page.waitForTimeout(SETTLE_MS);
    const lead = (Date.now() - opened) / 1000;

    await script(page);

    const facts: string[] = [];
    for (const fact of part.facts ?? []) {
      const scope = fact.within ? page.locator(fill(fact.within)) : page.locator("body");
      await expect(scope.getByText(new RegExp(fact.text)).first(), `fact on screen: ${fact.text}`).toBeVisible({ timeout: 30_000 });
      facts.push(fact.within ? `${fact.text} (within ${fill(fact.within)})` : fact.text);
    }

    const acted = (Date.now() - opened) / 1000 - lead;
    expect(acted, `beat ${beat.id} finishes its actions inside min_seconds`).toBeLessThan(beat.min_seconds);
    if (acted < hold) {
      await page.waitForTimeout((hold - acted) * 1000);
    }
    const recorded = (Date.now() - opened) / 1000;
    const video = page.video();
    await context.close();
    expect(video, "the context recorded a video").not.toBeNull();
    await video?.saveAs(join(clipsDir, `${beat.id}.webm`));
    await video?.delete();

    writeFileSync(
      join(clipsDir, `${beat.id}.json`),
      JSON.stringify(
        {
          format: "agon.video-clip/1",
          beat: beat.id,
          release_sha: releaseSha,
          data_commit: served,
          site,
          run_id: runId,
          route: fill(part.source.slice("web:".length)),
          viewport: part.viewport ?? "desktop",
          framing,
          theme: sheet.theme,
          lead_seconds: Number(lead.toFixed(3)),
          actions_seconds: Number(acted.toFixed(3)),
          hold_seconds: hold,
          recorded_seconds: Number(recorded.toFixed(3)),
          facts_checked: facts,
          page_errors: pageErrors,
          placeholder: null,
          captured_at: new Date().toISOString(),
        },
        null,
        2,
      ) + "\n",
    );
  });
}
