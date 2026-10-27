/*
  Submission video capture, CI only. One clip per web part of video/beats.json,
  recorded from the LIVE site at a stated release SHA, never from a local build,
  plus one PNG per still that ci/video/stills.py built and checked. A still part
  with "animate": true (the opening) is recorded as a clip instead, at 1920 x 930:
  every CSS animation is paused at zero while the page settles, the lead mark is
  taken, then they play, so compose.py trims to the animation's first frame.

  Not a console test: the file has no .spec suffix, so the default
  playwright.config.ts never runs it; playwright.video.config.ts does, from
  .github/workflows/video.yml (main only) and video-check.yml (the pull request
  rehearsal). Nothing here runs on a developer machine.

  Per web clip:
  1. A fresh context at the beat's framing (1920 x 1080, or 375 x 812 for a
     phone beat), dark theme, first-run guide skipped, recording.
  2. Before anything is used, the page's body data-commit must equal
     AGON_VIDEO_RELEASE_SHA. The run is the one the sheet names, or with
     run_id "default" the first run in the live runs/index.json, which must be
     a pipeline run. Its id, seed and clubs come from its run file: routes and
     facts use them ({run_id}, {seed}, {home}, {away}), and the Match Centre
     picks that seed in Match setup, so a new recorded run needs no edit.
  3. The lead mark: compose.py trims everything before it.
  4. The beat's script acts on stable hooks (data-view, data-run-id, roles).
  5. Every fact the beat lists must hold: text on screen, text in a file the
     live site serves (runs/<run>/manifest.json), or text in this repository
     (src/agon/fleet/steps.py, for a rule the code makes true on every run). The narration says it, so with
     AGON_VIDEO_FACTS=enforce (the default, and video.yml) a false sentence
     fails here instead of reaching a judge; with report (the rehearsal) it is
     written into the clip receipt and the run goes on.
  6. The page is held until max_seconds plus a margin after the lead, so the
     clip covers whatever length the narration measures.
  A receipt <beat>.json beside <beat>.webm records the release, the commit the
  page served, the run, the lead, the hold and the facts.

  Environment: AGON_VIDEO_BEATS (path to beats.json), AGON_VIDEO_RELEASE_SHA,
  AGON_VIDEO_CLIPS (output folder), optional AGON_VIDEO_ONLY (comma-separated
  beat ids, default all), AGON_VIDEO_FACTS (enforce or report),
  AGON_VIDEO_STILLS (the folder stills.py wrote) and AGON_VIDEO_SITE.
*/
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";

interface Fact {
  text: string;
  within?: string;
  file?: string;
  repo?: string;
}

interface RunInfo {
  id: string;
  seed: number;
  home: string;
  away: string;
}

interface Part {
  source: string;
  viewport?: "desktop" | "phone";
  seconds?: number;
  actions?: string;
  facts?: Fact[];
  animate?: boolean;
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

interface StillMeta {
  beat: string;
  still: string;
  run_id: string;
  data_commit: string;
  facts_checked: string[];
  facts_failed: string[];
  placeholder: string | null;
}

/* Device scale 1: Playwright's recorder takes frames at CSS size, so a scale of 2 would only pad the frame. */
const FRAMING = {
  desktop: { width: 1920, height: 1080, scale: 1 },
  phone: { width: 375, height: 812, scale: 1 },
} as const;
const STILL = { width: 1920, height: 930 };
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
const repoRoot = resolve(dirname(required("AGON_VIDEO_BEATS")), "..");
const releaseSha = required("AGON_VIDEO_RELEASE_SHA");
const clipsDir = required("AGON_VIDEO_CLIPS");
const stillsDir = process.env.AGON_VIDEO_STILLS ?? "";
const site = (process.env.AGON_VIDEO_SITE || sheet.site).replace(/\/+$/, "");
const factsMode = process.env.AGON_VIDEO_FACTS === "report" ? "report" : "enforce";
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

/* Seconds the web part may spend on its actions: its own length, or the beat's minimum less the named parts. */
function actionBudget(beat: Beat, part: Part): number {
  if (part.seconds) {
    return part.seconds;
  }
  return beat.min_seconds - parts(beat).reduce((sum, p) => sum + (p.seconds ?? 0), 0);
}

function fill(text: string, run: RunInfo): string {
  return text
    .replaceAll("{run_id}", run.id)
    .replaceAll("{seed}", String(run.seed))
    .replaceAll("{home}", run.home)
    .replaceAll("{away}", run.away);
}

/* A fact's pattern, with the run's values put in as literal text. */
function pattern(text: string, run: RunInfo): RegExp {
  const literal = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    text
      .replaceAll("{run_id}", literal(run.id))
      .replaceAll("{seed}", String(run.seed))
      .replaceAll("{home}", literal(run.home))
      .replaceAll("{away}", literal(run.away)),
  );
}

/* The run the video shows, read from the live site (never a hard-coded id when the sheet says default). */
async function resolveRun(page: Page): Promise<RunInfo> {
  const index = (await (await page.request.get(`${site}/runs/index.json`)).json()) as {
    runs: { run_id: string; provenance: string }[];
  };
  let id = sheet.run_id;
  if (id !== "default") {
    expect(
      index.runs.map((row) => row.run_id),
      `the live site lists run ${id}`,
    ).toContain(id);
  } else {
    const first = index.runs[0];
    expect(first?.provenance, `the live site's default run ${first?.run_id} is a pipeline run`).toBe("pipeline");
    id = first.run_id;
  }
  const response = await page.request.get(`${site}/runs/${id}.json`);
  expect(response.ok(), `the live site serves runs/${id}.json`).toBe(true);
  const run = (await response.json()) as { run_id: string; seed: number; home: { name: string }; away: { name: string } };
  expect(run.run_id, `runs/${id}.json names its own run`).toBe(id);
  return { id, seed: run.seed, home: run.home.name, away: run.away.name };
}

/* Change the hash route in place, as a click on a nav link would. */
async function go(page: Page, route: string): Promise<void> {
  await page.evaluate((hash) => {
    window.location.hash = hash;
  }, route);
}

/* Bring an element into view with a visible scroll. */
async function glide(page: Page, target: Locator, block: "start" | "center" = "center"): Promise<void> {
  await target.first().evaluate((element, where) => element.scrollIntoView({ behavior: "smooth", block: where }), block);
  await page.waitForTimeout(1200);
}

type Script = (page: Page, run: RunInfo) => Promise<void>;

/* Steps before the lead mark, trimmed out of the clip: a click scrolls the page
   to its target, so controls are set here and the clip opens where it should. */
const PREPARE: Record<string, Script> = {
  "match-centre": async (page, run) => {
    const view = page.locator('[data-view="home"]');
    await expect(view.locator('section[data-state="ready"]')).toBeVisible({ timeout: 30_000 });
    /* The Match Centre opens on the lowest seed; pick the recorded run's match. */
    await view.getByRole("group", { name: "Seed" }).locator(`label:has(> input[value="${run.seed}"])`).click();
    await expect(view.getByText(new RegExp(`seed ${run.seed}\\.`)).filter({ visible: true }).first()).toBeVisible({ timeout: 30_000 });
    await expect(view.locator('section[data-state="ready"]')).toBeVisible({ timeout: 30_000 });
    await view.getByRole("group", { name: "Replay speed" }).locator('label:has(> input[value="60"])').click();
    await view.getByRole("button", { name: "Play", exact: true }).click();
    await view.locator('[aria-labelledby="h-replay"]').evaluate((element) => {
      element.scrollIntoView({ block: "start" });
      window.scrollBy(0, -64);
    });
  },
};

/* One script per beat id; each acts on hooks the console's own tests rely on. */
const SCRIPTS: Record<string, Script> = {
  "match-centre": async (page) => {
    const view = page.locator('[data-view="home"]');
    /* Opens on the scoreboard with the replay already running (see PREPARE). */
    await page.waitForTimeout(3500);
    /* The interpret charts (lane D4): win probability, then team stats with expected goals. */
    await glide(page, view.getByRole("heading", { name: "Win probability", exact: true }), "start");
    await page.waitForTimeout(2200);
    await glide(page, view.getByRole("heading", { name: "Team stats", exact: true }), "start");
    await page.waitForTimeout(1200);
  },
  newsroom: async (page, run) => {
    await go(page, `#/newsroom/${run.id}`);
    const view = page.locator('[data-view="newsroom"]');
    await expect(view).toBeVisible();
    await view.getByRole("button", { name: "Replay the run" }).click();
    await expect(view.getByText(/Showing every step, \d+ in all\./)).toBeVisible({ timeout: 20_000 });
  },
  published: async (page, run) => {
    await go(page, `#/recap/${run.id}`);
    const view = page.locator('[data-view="recap"]');
    await expect(view.locator("#recap-label")).toBeVisible({ timeout: 20_000 });
    for (const tab of [/^EL/, /^ES/, /^FR/, /^Easy-read/]) {
      await page.waitForTimeout(1100);
      await view.getByRole("tab", { name: tab }).click();
    }
    await page.waitForTimeout(600);
  },
  verify: async (page, run) => {
    await go(page, "#/verify");
    const view = page.locator('[data-view="verify"]');
    await expect(view).toBeVisible();
    await expect(view.locator('[aria-labelledby="h-bundle"]')).toHaveAttribute("data-state", "done", { timeout: 30_000 });
    await page.waitForTimeout(1500);
    await glide(page, view.locator(`section[data-run-id="${run.id}"]`).getByText(/Approved hash and published hash/));
  },
};

/* Every fact of a part, checked on the page, in a served file or in the repository; returns [held, failed]. */
async function checkFacts(page: Page, facts: Fact[], run: RunInfo): Promise<[string[], string[]]> {
  const held: string[] = [];
  const failed: string[] = [];
  for (const fact of facts) {
    const where = fact.file ? ` (in ${fill(fact.file, run)})` : fact.repo ? ` (in the repository, ${fact.repo})` : fact.within ? ` (within ${fill(fact.within, run)})` : "";
    const label = `${fill(fact.text, run)}${where}`;
    const re = pattern(fact.text, run);
    let ok = false;
    if (fact.file) {
      const response = await page.request.get(`${site}/${fill(fact.file, run)}`);
      ok = response.ok() && re.test(await response.text());
    } else if (fact.repo) {
      const path = join(repoRoot, fact.repo);
      ok = existsSync(path) && re.test(readFileSync(path, "utf-8"));
    } else {
      const scope = fact.within ? page.locator(fill(fact.within, run)) : page.locator("body");
      ok = await scope
        .getByText(re)
        .filter({ visible: true })
        .first()
        .waitFor({ state: "visible", timeout: factsMode === "enforce" ? 30_000 : 5_000 })
        .then(() => true)
        .catch(() => false);
    }
    if (ok) {
      held.push(label);
    } else if (factsMode === "enforce") {
      throw new Error(`a narrated fact does not hold on the live site at ${releaseSha}: ${label}`);
    } else {
      failed.push(label);
    }
  }
  return [held, failed];
}

async function recordContext(browser: Browser, framing: (typeof FRAMING)[keyof typeof FRAMING]) {
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
  return context;
}

const known = new Set(sheet.beats.map((beat) => beat.id));
const unknown = only.filter((name) => name !== "all" && !known.has(name));
if (unknown.length) {
  throw new Error(`AGON_VIDEO_ONLY names beats the sheet does not have: ${unknown.join(", ")}`);
}
const chosen = sheet.beats.filter((beat) => only.includes("all") || only.includes(beat.id));

for (const beat of chosen.filter((b) => webPart(b))) {
  test(`capture ${beat.id}`, async ({ browser }) => {
    const part = webPart(beat) as Part;
    const script = SCRIPTS[beat.id];
    expect(script, `a capture script exists for web beat ${beat.id}`).toBeDefined();
    const hold = beat.max_seconds + MARGIN_SECONDS;
    test.setTimeout((hold + 150) * 1000);
    const framing = FRAMING[part.viewport ?? "desktop"];
    mkdirSync(join(clipsDir, "raw"), { recursive: true });

    const context = await recordContext(browser, framing);
    const page = await context.newPage();
    const opened = Date.now();
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${site}/`, { waitUntil: "load" });
    const served = await page.locator("body").getAttribute("data-commit");
    expect(served, `the live site serves the release SHA ${releaseSha}`).toBe(releaseSha);
    await page.locator('html[data-app="ready"]').waitFor({ state: "attached", timeout: 30_000 });
    const run = await resolveRun(page);
    await PREPARE[beat.id]?.(page, run);
    await page.waitForTimeout(SETTLE_MS);
    const lead = (Date.now() - opened) / 1000;

    await script(page, run);
    const scripted = (Date.now() - opened) / 1000 - lead;
    const [held, failed] = await checkFacts(page, part.facts ?? [], run);

    /* Enforced, every fact must be on screen inside the budget too; reported, a missing fact's wait is not the clip's. */
    const acted = factsMode === "enforce" ? (Date.now() - opened) / 1000 - lead : scripted;
    const budget = actionBudget(beat, part);
    expect(acted, `beat ${beat.id} finishes its actions and facts inside ${budget} s`).toBeLessThan(budget);
    const elapsed = (Date.now() - opened) / 1000 - lead;
    if (elapsed < hold) {
      await page.waitForTimeout((hold - elapsed) * 1000);
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
          run_id: run.id,
          seed: run.seed,
          route: fill(part.source.slice("web:".length), run),
          viewport: part.viewport ?? "desktop",
          framing,
          theme: sheet.theme,
          lead_seconds: Number(lead.toFixed(3)),
          actions_seconds: Number(acted.toFixed(3)),
          hold_seconds: hold,
          recorded_seconds: Number(recorded.toFixed(3)),
          facts_mode: factsMode,
          facts_checked: held,
          facts_failed: failed,
          page_errors: pageErrors,
          placeholder: null,
          captured_at: new Date().toISOString(),
        },
        null,
        2,
      ) + "\n",
    );
    if (failed.length) {
      console.log(`REPORTED, not on the live site for beat ${beat.id}: ${failed.join("; ")}`);
    }
  });
}

for (const beat of chosen) {
  for (const part of parts(beat).filter((p) => p.source.startsWith("still:"))) {
    const name = part.source.slice("still:".length);
    const animated = part.animate === true;
    test(`still ${name} for ${beat.id}`, async ({ browser }) => {
      expect(stillsDir, "AGON_VIDEO_STILLS names the folder ci/video/stills.py wrote").not.toBe("");
      const metaPath = join(stillsDir, `still-${name}.meta.json`);
      expect(existsSync(metaPath), `ci/video/stills.py wrote ${metaPath}`).toBe(true);
      const meta = JSON.parse(readFileSync(metaPath, "utf-8")) as StillMeta;
      mkdirSync(clipsDir, { recursive: true });
      /* An animated still is held, like a web clip, for max_seconds plus a margin after its lead. */
      const hold = beat.max_seconds + MARGIN_SECONDS;
      let lead: number | null = null;
      let recorded: number | null = null;
      if (!meta.placeholder && animated) {
        test.setTimeout((hold + 150) * 1000);
        mkdirSync(join(clipsDir, "raw"), { recursive: true });
        const context = await browser.newContext({
          viewport: STILL,
          deviceScaleFactor: 1,
          colorScheme: "dark",
          reducedMotion: "no-preference",
          recordVideo: { dir: join(clipsDir, "raw"), size: STILL },
        });
        const page = await context.newPage();
        const opened = Date.now();
        await page.setContent(readFileSync(join(stillsDir, `still-${name}.html`), "utf-8"), { waitUntil: "load" });
        /* Hold every animation at its start while the page settles, so the frames before the lead show nothing ahead of it. */
        const animations = await page.evaluate(() => {
          const all = document.getAnimations();
          for (const animation of all) {
            animation.pause();
            animation.currentTime = 0;
          }
          return all.length;
        });
        expect(animations, `still ${name} is animate in the sheet, so its page runs CSS animations`).toBeGreaterThan(0);
        await page.evaluate(async () => {
          await document.fonts.ready;
        });
        await page.waitForTimeout(SETTLE_MS);
        /* The lead mark: compose.py trims everything before it, and the animations start here. */
        lead = (Date.now() - opened) / 1000;
        await page.evaluate(() => {
          for (const animation of document.getAnimations()) {
            animation.play();
          }
        });
        await page.waitForTimeout(hold * 1000);
        recorded = (Date.now() - opened) / 1000;
        const video = page.video();
        await context.close();
        expect(video, "the context recorded a video").not.toBeNull();
        await video?.saveAs(join(clipsDir, `still-${name}.webm`));
        await video?.delete();
      } else if (!meta.placeholder) {
        const context = await browser.newContext({ viewport: STILL, deviceScaleFactor: 1, colorScheme: "dark" });
        const page = await context.newPage();
        await page.setContent(readFileSync(join(stillsDir, `still-${name}.html`), "utf-8"), { waitUntil: "load" });
        await page.evaluate(async () => {
          await document.fonts.ready;
        });
        await page.screenshot({ path: join(clipsDir, `still-${name}.png`) });
        await context.close();
      }
      writeFileSync(
        join(clipsDir, `still-${name}.json`),
        JSON.stringify(
          {
            format: "agon.video-clip/1",
            beat: `still-${name}`,
            for_beat: beat.id,
            release_sha: releaseSha,
            data_commit: meta.data_commit,
            site,
            run_id: meta.run_id,
            animated,
            ...(lead === null
              ? {}
              : {
                  framing: STILL,
                  lead_seconds: Number(lead.toFixed(3)),
                  hold_seconds: hold,
                  recorded_seconds: Number((recorded ?? 0).toFixed(3)),
                }),
            facts_mode: factsMode,
            facts_checked: meta.facts_checked,
            facts_failed: meta.facts_failed,
            placeholder: meta.placeholder,
            captured_at: new Date().toISOString(),
          },
          null,
          2,
        ) + "\n",
      );
    });
  }
}
