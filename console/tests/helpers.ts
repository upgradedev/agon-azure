/* Paths and a disk-backed fetch for tests: the same files the deploy ships. */
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { FetchText } from "../src/lib/loader";

export const REPO = fileURLToPath(new URL("../../", import.meta.url));
export const WEB = join(REPO, "web");
export const CASES = join(REPO, "tests", "js");

export function readJson<T = unknown>(path: string): T {
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

/* The export of tests/recorded_kit.py by agon.webrecord, held byte for byte by tests/test_webrecord.py. */
export const RECORDED = join(REPO, "tests", "fixtures", "recorded-run");

/* Answers relative URLs such as runs/index.json from a folder, as the prerender
   answers them from dist/ (which is a copy of web/runs and web/data-fixture). */
export function diskFetchAt(root: string): FetchText {
  return async (url) => {
    const target = normalize(join(root, url));
    if (!target.startsWith(normalize(root) + sep) || !existsSync(target) || !statSync(target).isFile()) {
      return { ok: false, status: 404, text: "" };
    }
    return { ok: true, status: 200, text: readFileSync(target, "utf8") };
  };
}

export const diskFetch: FetchText = diskFetchAt(WEB);
