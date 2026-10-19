/*
  The prerendered page, checked before any build: render the console the way
  scripts/prerender.mjs does and hold the markup to what the gates need.

  - every readiness marker of the demo target in ci/readiness.json, verbatim,
    including the run labels anchored to their screens' ids;
  - the stand-in card header and the not-deployed sentence, verbatim;
  - no style attribute, no inline script, no <style> (the CSP would block
    them); no duplicate id; every aria and label reference resolves; and the
    commit placeholder never appears in the app markup.
*/
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { prerender } from "../src/entry-server";
import { DESIGN_PREVIEW_LABEL, NOT_DEPLOYED_SENTENCE, STANDIN_HEADER } from "../src/lib/runs";
import { PREVIEW_ONLY_SENTENCE } from "../src/lib/destinations";
import { REPO, diskFetch } from "./helpers";

let html = "";

beforeAll(async () => {
  const result = await prerender(diskFetch);
  expect(result.ok, result.message).toBe(true);
  html = result.html;
});

describe("prerendered markup", () => {
  it("carries every demo marker from ci/readiness.json", () => {
    const config = JSON.parse(readFileSync(join(REPO, "ci", "readiness.json"), "utf8")) as {
      targets: { name: string; markers: string[] }[];
    };
    const demo = config.targets.find((t) => t.name === "demo");
    expect(demo).toBeDefined();
    for (const marker of demo?.markers ?? []) {
      expect(html, marker).toContain(marker);
    }
    expect(html).toContain(`id="wall-label">${DESIGN_PREVIEW_LABEL}`);
    expect(html).toContain(`id="approve-label">${DESIGN_PREVIEW_LABEL}`);
    expect(html).toContain(`id="recap-label">Not approved. ${DESIGN_PREVIEW_LABEL}`);
  });

  it("states the stand-in card header, the not-deployed sentence and the preview-only sentence", () => {
    expect(html).toContain(STANDIN_HEADER);
    expect(html).toContain(NOT_DEPLOYED_SENTENCE);
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
    for (const view of ["home", "newsroom", "approve", "recap", "verify", "data-lab", "how-it-works", "about", "not-found"]) {
      expect(html).toContain(`data-view="${view}"`);
    }
    expect(html).toMatch(/<section data-view="home" aria-labelledby="h-home">/);
    expect(html).toMatch(/<section data-view="verify" hidden="" aria-labelledby="h-verify">/);
  });

  it("uses no dash characters", () => {
    const dashes = new RegExp(`[${String.fromCharCode(0x2013, 0x2014, 0x2015)}]`);
    expect(html).not.toMatch(dashes);
  });
});
