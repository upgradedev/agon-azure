/*
  Listen on the recap, for the recorded run on main: the spoken report of the
  edition on screen comes from the bundle (audio-<edition>.mp3), and the hand-
  written sample says it has no audio instead of showing a player.
*/
import { join } from "node:path";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Listen, captionsNote, disclosureLine, type Captions } from "../src/components/listen";
import type { BundleManifest, Run } from "../src/lib/types";
import { WEB, readJson } from "./helpers";

function clean(html: string): string {
  return html.replace(/<!-- -->/g, "");
}

describe("Listen", () => {
  it("plays the recorded run's own audio for the edition on screen", () => {
    const index = readJson<{ runs: { run_id: string; file: string; provenance: string }[] }>(join(WEB, "runs", "index.json"));
    const row = index.runs.find((r) => r.provenance === "pipeline");
    if (!row) {
      return; /* a tree without a recorded run: the sample case below still runs */
    }
    const run = readJson<Run>(join(WEB, "runs", row.file));
    const manifest = readJson<BundleManifest>(join(WEB, "runs", row.run_id, "manifest.json"));
    const html = clean(renderToString(<Listen run={run} manifest={manifest} edition="el" />));
    expect(html).toContain("Listen to the Greek report");
    expect(html).toContain(`runs/${row.run_id}/audio-el.mp3`);
    expect(html).toContain('aria-live="polite"');
    expect(html).not.toMatch(/\sstyle=/i);
  });

  it("says the hand-written sample has no audio", () => {
    const run = readJson<Run>(join(WEB, "runs", "sample-s1.json"));
    const manifest = readJson<BundleManifest>(join(WEB, "runs", "sample-s1", "manifest.json"));
    const html = clean(renderToString(<Listen run={run} manifest={manifest} edition="en" />));
    expect(html).toContain("The hand-written sample has no audio.");
    expect(html).not.toContain("<audio");
  });
});

describe("the captions notes", () => {
  const base: Captions = { edition: "el", language: "el", disclosure: "AI disclosure", voice: null, sentences: [], word_timings: null };

  it("says the Greek goal line cannot be cut out only when there are no word timings", () => {
    expect(captionsNote(base, "el")).toContain("The Greek goal line");
    const timed = captionsNote({ ...base, word_timings: [{ start_ms: 0 }] }, "el");
    expect(timed).toContain("carry word timings");
    expect(timed).not.toContain("cannot be cut out");
    expect(captionsNote({ ...base, edition: "en" }, "en")).not.toContain("Greek");
  });

  it("never leaves the disclosure line on loading when the captions failed or are missing", () => {
    expect(disclosureLine(base, "", true)).toBe("AI disclosure");
    expect(disclosureLine(null, "the captions could not be read", true)).toContain("could not be read");
    expect(disclosureLine(null, "", false)).toContain("no captions file");
    expect(disclosureLine(null, "", true)).toBe("an AI disclosure, whose words are still loading");
  });
});
