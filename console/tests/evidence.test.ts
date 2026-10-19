/*
  Every file-and-line link on "How Agon works" and the Data Lab must point at
  a line that holds what the label says. The files are read at EVIDENCE_SHA
  with git (the console workflow checks out the full history), so a later
  change on main can never move a line under its link.
*/
import { execFileSync } from "node:child_process";
import { describe, expect, it } from "vitest";
import { DATA_LAB_EVIDENCE, EVIDENCE_SHA, PIPELINE, evidenceHref, type EvidenceItem } from "../src/lib/evidence";
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

  it("link runs and files on the public repository only", () => {
    for (const item of items) {
      expect(evidenceHref(item)).toMatch(/^https:\/\/github\.com\/upgradedev\/agon-azure\/(?:blob\/[0-9a-f]{40}\/|actions\/runs\/[0-9]+$)/);
    }
  });

  it("give every pipeline node a status sentence and at least one piece of evidence", () => {
    for (const node of PIPELINE) {
      expect(node.evidence.length, node.id).toBeGreaterThan(0);
      expect(node.statusText.endsWith("."), node.id).toBe(true);
      expect(node.summary.endsWith("."), node.id).toBe(true);
    }
  });
});
