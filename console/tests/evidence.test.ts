/*
  Every file-and-line link on "How Agon works" and the Data Lab must point at
  a line that holds what the label says. The files are read at EVIDENCE_SHA
  with git (the console workflow checks out the full history), so a later
  change on main can never move a line under its link.

  A node marked "Ran in Azure for the recorded run" must cite that run: its
  job executions read from Azure, its sealed manifest on the public feed or
  the operator's drill that started it. The bundle hash it names is held to
  the committed copy of that manifest, byte for byte.
*/
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  DATA_LAB_EVIDENCE,
  DRILL_RUN_ID,
  EVIDENCE_SHA,
  FEED_URL,
  PIPELINE,
  RECORDED_BUNDLE,
  RECORDED_EXECUTIONS,
  evidenceHref,
  type EvidenceItem,
} from "../src/lib/evidence";
import { REPO } from "./helpers";

function fileAt(path: string): string {
  return execFileSync("git", ["show", `${EVIDENCE_SHA}:${path}`], { cwd: REPO, encoding: "utf8", maxBuffer: 16 * 1024 * 1024 });
}

const items: EvidenceItem[] = [...PIPELINE.flatMap((node) => node.evidence), ...DATA_LAB_EVIDENCE];

describe("evidence links", () => {
  it("cite only lines that hold their snippet at the pinned commit", () => {
    const files = items.filter((item): item is Extract<EvidenceItem, { kind: "file" }> => item.kind === "file");
    expect(files.length).toBeGreaterThan(15);
    for (const item of files) {
      const lines = fileAt(item.path).split("\n");
      expect(lines.length, item.path).toBeGreaterThanOrEqual(item.line);
      expect(lines[item.line - 1], `${item.path}:${item.line}`).toContain(item.snippet);
    }
  });

  it("link runs and files on the public repository, and feed files on the public feed container, only", () => {
    expect(FEED_URL).toMatch(/^https:\/\/[a-z0-9]+\.blob\.core\.windows\.net\/feed\/$/);
    for (const item of items) {
      if (item.kind === "feed") {
        expect(evidenceHref(item)).toBe(`${FEED_URL}${item.path}`);
        expect(item.path).toMatch(/^[0-9a-f]{64}\/manifest\.json$/);
      } else {
        expect(evidenceHref(item)).toMatch(/^https:\/\/github\.com\/upgradedev\/agon-azure\/(?:blob\/[0-9a-f]{40}\/|actions\/runs\/[0-9]+$)/);
      }
    }
  });

  it("give every pipeline node a status sentence and at least one piece of evidence", () => {
    for (const node of PIPELINE) {
      expect(node.evidence.length, node.id).toBeGreaterThan(0);
      expect(node.statusText.endsWith("."), node.id).toBe(true);
      expect(node.summary.endsWith("."), node.id).toBe(true);
    }
  });

  it("make every node that ran in Azure cite the recorded run, and name that run's real bundle", () => {
    const committed = readFileSync(join(REPO, "web", "runs", `run-${RECORDED_BUNDLE.slice(0, 12)}`, "manifest.json"));
    expect(createHash("sha256").update(committed).digest("hex")).toBe(RECORDED_BUNDLE);
    const executions = fileAt(RECORDED_EXECUTIONS);
    expect(executions).toContain(`"bundle_sha256": "${RECORDED_BUNDLE}"`);
    expect(executions).toContain(`-${DRILL_RUN_ID}"`);
    const azure = PIPELINE.filter((node) => node.status === "azure");
    expect(azure.length).toBeGreaterThan(5);
    for (const node of azure) {
      const cites = node.evidence.some(
        (item) =>
          (item.kind === "file" && item.path === RECORDED_EXECUTIONS) ||
          (item.kind === "feed" && item.path === `${RECORDED_BUNDLE}/manifest.json`) ||
          (item.kind === "run" && item.runId === DRILL_RUN_ID),
      );
      expect(cites, node.id).toBe(true);
    }
  });

  it("never claim what is not deployed (wording the readiness audit of 2026-10-20 found)", () => {
    const text = PIPELINE.map((node) => `${node.title} ${node.statusText} ${node.summary}`).join("\n");
    for (const stale of [
      "without anyone asking",
      "only through a read-only Model Context Protocol server",
      "Every channel calls the same verifier",
      "The decision endpoint is not deployed",
      "Nothing has been published from this site",
      "written by hand instead",
      "a correction becomes a new bundle",
      "Verify recomputes every hash",
    ]) {
      expect(text, stale).not.toContain(stale);
    }
    const mcp = PIPELINE.find((node) => node.id === "mcp");
    expect(mcp?.statusText).toContain("Not deployed");
    const start = PIPELINE.find((node) => node.id === "start");
    expect(start?.statusText).toContain("No automatic trigger at the final whistle is deployed.");
  });
});
