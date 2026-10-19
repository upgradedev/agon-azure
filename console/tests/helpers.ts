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

/* Answers relative URLs such as runs/index.json from web/, as the prerender
   answers them from dist/ (which is a copy of web/runs and web/data-fixture). */
export const diskFetch: FetchText = async (url) => {
  const target = normalize(join(WEB, url));
  if (!target.startsWith(normalize(WEB) + sep) || !existsSync(target) || !statSync(target).isFile()) {
    return { ok: false, status: 404, text: "" };
  }
  return { ok: true, status: 200, text: readFileSync(target, "utf8") };
};
