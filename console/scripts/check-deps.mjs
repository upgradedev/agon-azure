/*
  Dependency pins for the console, checked before anything installs.

  Rules, each a hard failure:
  1. every dependency and devDependency in package.json is an exact version
     (digits and dots only: no ^, ~, range, tag, URL or workspace link);
  2. with --lock: package-lock.json exists, is lockfileVersion 3, lists the
     same direct dependencies with the same specs as package.json, and
     resolves each one to exactly the pinned version.

  npm ci also refuses a lock that is out of sync; this check says which entry
  differs in words, and it runs before npm touches the network.

  Dependency-free Node. Usage: node scripts/check-deps.mjs [--lock]
*/
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const consoleDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const EXACT = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const problems = [];

const pkg = JSON.parse(readFileSync(join(consoleDir, "package.json"), "utf8"));
const direct = { ...(pkg.dependencies ?? {}), ...(pkg.devDependencies ?? {}) };
const names = Object.keys(direct).sort();
if (!names.length) {
  problems.push("package.json lists no dependencies; a check that scanned nothing proves nothing");
}
for (const name of names) {
  if (!EXACT.test(direct[name])) {
    problems.push(`${name}: "${direct[name]}" is not an exact version`);
  }
}

if (process.argv.includes("--lock")) {
  const lockPath = join(consoleDir, "package-lock.json");
  if (!existsSync(lockPath)) {
    problems.push("package-lock.json is missing; it comes from the lock job artifact and must be committed");
  } else {
    const lock = JSON.parse(readFileSync(lockPath, "utf8"));
    if (lock.lockfileVersion !== 3) {
      problems.push(`package-lock.json is lockfileVersion ${lock.lockfileVersion}, expected 3`);
    }
    const rootEntry = (lock.packages ?? {})[""] ?? {};
    const locked = { ...(rootEntry.dependencies ?? {}), ...(rootEntry.devDependencies ?? {}) };
    const lockedNames = Object.keys(locked).sort();
    if (JSON.stringify(lockedNames) !== JSON.stringify(names)) {
      problems.push(`the lock lists direct dependencies ${lockedNames.join(", ")}; package.json lists ${names.join(", ")}`);
    }
    for (const name of names) {
      if (locked[name] !== undefined && locked[name] !== direct[name]) {
        problems.push(`${name}: the lock records the spec "${locked[name]}", package.json says "${direct[name]}"`);
      }
      const resolved = (lock.packages ?? {})[`node_modules/${name}`];
      if (!resolved) {
        problems.push(`${name}: the lock does not resolve it`);
      } else if (resolved.version !== direct[name]) {
        problems.push(`${name}: the lock resolves ${resolved.version}, package.json pins ${direct[name]}`);
      }
    }
  }
}

if (problems.length) {
  console.error("check-deps: FAIL");
  for (const p of problems) {
    console.error(`  ${p}`);
  }
  process.exit(1);
}
console.log(`check-deps: PASS (${names.length} direct dependencies, exact pins${process.argv.includes("--lock") ? ", lock agrees" : ""})`);
