/*
  Build-time prerender: turns dist/index.html into the full console as static
  HTML, so the served page carries every marker the readiness gate and
  ci/web_check.py look for (the run labels anchored to id="wall-label",
  id="approve-label" and id="recap-label", "Match Centre", "Newsroom Wall",
  data-commit) without running a script.

  Steps, each a hard failure:
  1. copy the static data the console reads from web/ (one source of truth):
     web/runs and web/data-fixture into dist/;
  2. render the app through dist-ssr/entry-server.js, which loads those files
     with the same loader and validators the browser uses;
  3. put the markup into the one <!--app-html--> slot of dist/index.html;
  4. refuse a page that carries an inline style or an inline script, and a
     commit placeholder anywhere but once in index.html (ci/stamp_web.py
     stamps exactly one and fails on leftovers);
  5. delete dist-ssr, which must never ship.

  Options, for the console workflow's recorded-run rehearsal only (the deploy
  runs it without any): --site DIR renders into another unrendered client
  build instead of dist, and --runs DIR copies that runs folder instead of
  web/runs (web/runs plus an exported recorded run, listed first).

  Dependency-free Node: fs, path and url only.
*/
import { cpSync, existsSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, normalize, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const consoleDir = resolve(here, "..");
const repo = resolve(consoleDir, "..");
const ssrDir = join(consoleDir, "dist-ssr");

function option(name) {
  const at = process.argv.indexOf(name);
  if (at === -1) {
    return null;
  }
  const value = process.argv[at + 1];
  if (!value || value.startsWith("--")) {
    console.error(`prerender: FAIL: ${name} needs a directory`);
    process.exit(1);
  }
  return resolve(value);
}

const known = new Set(["--site", "--runs"]);
for (const arg of process.argv.slice(2)) {
  if (arg.startsWith("--") && !known.has(arg)) {
    console.error(`prerender: FAIL: unknown option ${arg}`);
    process.exit(1);
  }
}
const dist = option("--site") ?? join(consoleDir, "dist");
const runsSource = option("--runs") ?? join(repo, "web", "runs");
const siteName = relative(consoleDir, dist) || ".";
const PLACEHOLDER = ["__AGON", "COMMIT__"].join("_");
const SLOT = "<!--app-html-->";
const TEXT = new Set([".html", ".js", ".css", ".json", ".svg", ".txt", ".xml", ".webmanifest"]);

function fail(message) {
  console.error(`prerender: FAIL: ${message}`);
  process.exit(1);
}

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      out.push(...walk(path));
    } else {
      out.push(path);
    }
  }
  return out;
}

if (!existsSync(join(dist, "index.html"))) {
  fail(`${siteName}/index.html is missing; run the client build first`);
}
const entry = join(ssrDir, "entry-server.js");
if (!existsSync(entry)) {
  fail("dist-ssr/entry-server.js is missing; run the server build first");
}

for (const [folder, from] of [
  ["runs", runsSource],
  ["data-fixture", join(repo, "web", "data-fixture")],
]) {
  if (!existsSync(from)) {
    fail(`${relative(repo, from) || from} is missing`);
  }
  rmSync(join(dist, folder), { recursive: true, force: true });
  cpSync(from, join(dist, folder), { recursive: true });
}

/* The loader asks for relative URLs such as runs/index.json; answer from dist. */
async function fetchText(url) {
  const target = normalize(join(dist, url));
  if (!target.startsWith(dist + sep) || !existsSync(target) || !statSync(target).isFile()) {
    return { ok: false, status: 404, text: "" };
  }
  return { ok: true, status: 200, text: readFileSync(target, "utf8") };
}

const { prerender } = await import(pathToFileURL(entry).href);
const result = await prerender(fetchText);
if (!result.ok) {
  fail(result.message);
}

const indexPath = join(dist, "index.html");
const template = readFileSync(indexPath, "utf8");
if (template.split(SLOT).length !== 2) {
  fail(`${siteName}/index.html must hold the slot ${SLOT} exactly once`);
}
const page = template.replace(SLOT, result.html);

if (/\sstyle=/i.test(result.html)) {
  fail("the prerendered markup holds a style attribute, which the CSP blocks");
}
if (/<style[\s>]/i.test(page)) {
  fail("the page holds a <style> element, which the CSP blocks");
}
for (const match of page.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
  if (!/\ssrc=/.test(match[1]) || match[2].trim()) {
    fail("the page holds an inline script, which the CSP blocks");
  }
}
if (page.split(PLACEHOLDER).length !== 2) {
  fail("index.html must hold the commit placeholder exactly once");
}
writeFileSync(indexPath, page);

for (const path of walk(dist)) {
  const dot = path.lastIndexOf(".");
  const ext = dot === -1 ? "" : path.slice(dot).toLowerCase();
  if (path !== indexPath && TEXT.has(ext) && readFileSync(path, "utf8").includes(PLACEHOLDER)) {
    fail(`the commit placeholder appears in ${relative(dist, path)}; ci/stamp_web.py would refuse the build`);
  }
}

rmSync(ssrDir, { recursive: true, force: true });
console.log(`prerender: wrote ${siteName}/index.html (${page.length} characters), copied ${relative(repo, runsSource) || runsSource} and web/data-fixture`);
