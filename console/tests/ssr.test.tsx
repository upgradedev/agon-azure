/*
  The prerendered page, checked before any build: render the console the way
  scripts/prerender.mjs does and hold the markup to what the gates need.

  - every readiness marker of the demo target in ci/readiness.json, verbatim,
    including the run labels anchored to their screens' ids;
  - the labels of the run shown by default, anchored the same way, also once a
    recorded run is listed first (ci/web_check.py page_label_markers);
  - the stand-in card header and the not-deployed sentence, verbatim;
  - no style attribute, no inline script, no <style> (the CSP would block
    them); no duplicate id; every aria and label reference resolves; and the
    commit placeholder never appears in the app markup.

  Every check runs twice: on web/runs as committed, and on web/runs as it reads
  after the exporter has put the recorded-run fixture first (helpers.afterFetch).
*/
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToString } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";
import { prerender } from "../src/entry-server";
import { BuildLabelView } from "../src/components/shell";
import { loadRunsData, type FetchText } from "../src/lib/loader";
import { DECIDED_CARD_SENTENCE, NOT_DEPLOYED_SENTENCE, STANDIN_HEADER, decidedMessage, editionLabel, flagGroups } from "../src/lib/runs";
import { PREVIEW_ONLY_SENTENCE } from "../src/lib/destinations";
import { REPO, afterFetch, diskFetch } from "./helpers";

const SOURCES: [string, FetchText, boolean][] = [
  ["as committed", diskFetch, true],
  ["with the recorded run listed first", afterFetch, false],
];

describe.each(SOURCES)("prerendered markup, %s", (_name, fetchText, committed) => {
  let html = "";
  let labels: string[] = [];
  let decided = false;
  let picks: string[] = [];
  let flagHeadings: string[] = [];

  beforeAll(async () => {
    const result = await prerender(fetchText);
    expect(result.ok, result.message).toBe(true);
    html = result.html;
    const data = await loadRunsData(fetchText);
    const run = data.runs[data.defaultRunId].run;
    labels = [`id="wall-label">${run.label}`, `id="approve-label">${run.label}`, `id="recap-label">${editionLabel(run)}`];
    decided = decidedMessage(run) !== null;
    picks = (data.runs[data.defaultRunId].analyst?.picks ?? []).map((p) => p.event_id);
    flagHeadings = flagGroups(run).map((g) => `${g.name} flags`);
  });

  /* The committed default run's sealed card carries the Analyst's turning points; the
     recorded-run fixture's card carries none, so its wall shows no Analyst desk. */
  it("shows the Analyst desk with every turning point the default run's sealed card carries, and none without them", () => {
    expect(picks.length > 0).toBe(committed);
    expect(html.includes('data-desk="analyst"')).toBe(picks.length > 0);
    expect([...html.matchAll(/\sdata-pick="([^"]+)"/g)].map((m) => m[1])).toEqual(picks);
    if (picks.length) {
      expect(html).toContain('<h2 id="h-analyst" class="text-sm font-semibold text-fg">Analyst desk: turning points</h2>');
      expect(html).toContain(">AI-written<");
    }
  });

  it("puts every flag on the approval card under the step that raised it", () => {
    /* The committed default run's one flag is the Fact Gate's; the recorded fixture's is the easy-read rules'. */
    expect(flagHeadings).toEqual(committed ? ["Fact Gate flags"] : ["Easy-read rules flags"]);
    for (const heading of flagHeadings) {
      expect(html, heading).toContain(`>${heading}</p>`);
    }
    expect(html).not.toContain(">Desk Editor flags</p>");
  });

  it("states the labels of the run it shows by default on every screen that shows it", () => {
    for (const label of labels) {
      expect(html, label).toContain(label);
    }
  });

  it("carries every demo marker from ci/readiness.json while that file names the run shown", () => {
    const config = JSON.parse(readFileSync(join(REPO, "ci", "readiness.json"), "utf8")) as {
      targets: { name: string; markers: string[] }[];
    };
    const demo = config.targets.find((t) => t.name === "demo");
    expect(demo).toBeDefined();
    const anchored = (demo?.markers ?? []).filter((m) => /^id="(wall|approve|recap)-label">/.test(m));
    expect(anchored.length).toBe(3);
    /* The committed config names the committed default run; the rehearsal rewrites it with
       python -m agon.webrecord --readiness, so here only the other markers must hold. */
    for (const marker of (demo?.markers ?? []).filter((m) => committed || !anchored.includes(m))) {
      expect(html, marker).toContain(marker);
    }
  });

  it("states the stand-in card header, the not-deployed or decided sentence and the preview-only sentence", () => {
    expect(html).toContain(STANDIN_HEADER);
    /* A decided run (a recorded run, approved on the real card) says so instead of "not deployed". */
    expect(html).toContain(decided ? DECIDED_CARD_SENTENCE : NOT_DEPLOYED_SENTENCE);
    expect(html).not.toContain(decided ? NOT_DEPLOYED_SENTENCE : DECIDED_CARD_SENTENCE);
    expect(html.split(PREVIEW_ONLY_SENTENCE).length - 1).toBe(2);
  });

  it("holds nothing the CSP would block", () => {
    expect(html).not.toMatch(/\sstyle=/i);
    expect(html).not.toMatch(/<style[\s>]/i);
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/\son[a-z]+=/i);
    expect(html).not.toContain(["__AGON", "COMMIT__"].join("_"));
  });

  it("has unique ids and every reference resolves", () => {
    const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]);
    const seen = new Set<string>();
    const dupes = ids.filter((id) => (seen.has(id) ? true : (seen.add(id), false)));
    expect(dupes).toEqual([]);
    const refs = [...html.matchAll(/\s(?:aria-labelledby|aria-describedby|aria-controls|for)="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/));
    const missing = refs.filter((ref) => !seen.has(ref));
    expect(missing).toEqual([]);
  });

  it("gives every disabled control a reason", () => {
    for (const match of html.matchAll(/<(button|input)\b[^>]*>/g)) {
      const tag = match[0];
      if (/\sdisabled(?:=""|\s|>)/.test(tag) || /aria-disabled="true"/.test(tag)) {
        expect(tag, tag).toMatch(/aria-describedby="[^"]+"/);
      }
    }
  });

  it("renders every view, with only the Match Centre visible", () => {
    for (const view of ["home", "newsroom", "approve", "recap", "verify", "data-lab", "media-office", "how-it-works", "about", "not-found"]) {
      expect(html).toContain(`data-view="${view}"`);
    }
    expect(html).toMatch(/<section data-view="home" aria-labelledby="h-home">/);
    expect(html).toMatch(/<section data-view="verify" hidden="" aria-labelledby="h-verify">/);
  });

  it("uses no dash characters", () => {
    const dashes = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0x2015)}]`);
    expect(html).not.toMatch(dashes);
  });

  it("prerenders the unstamped build label once, in the exact form ci/stamp_web.py rewrites", () => {
    expect(html.split(UNSTAMPED_P).length - 1).toBe(1);
  });
});

/* ci/stamp_web.py rewrites UNSTAMPED_P into STAMPED_P at deploy time, and the browser hydrates
   that markup with the commit read from <body data-commit>, so both must be exactly what
   BuildLabelView renders. tests/test_stamp_web.py holds the Python side to the same strings. */
const SHA = "0123456789abcdef0123456789abcdef01234567";
const UNSTAMPED_P = '<p class="text-xs text-muted" id="run-label">Preview build, commit not stamped (local build)</p>';
const STAMPED_P =
  '<p class="text-xs text-muted" id="run-label">Preview build, commit ' +
  `<a href="https://github.com/upgradedev/agon-azure/commit/${SHA}" aria-label="commit ${SHA}" class="font-mono text-link">0123456</a></p>`;

describe("the build label", () => {
  it("renders the markup ci/stamp_web.py writes, for a stamped commit and for none", () => {
    expect(renderToString(<BuildLabelView commit={SHA} />)).toBe(STAMPED_P);
    expect(renderToString(<BuildLabelView commit={null} />)).toBe(UNSTAMPED_P);
  });
});
