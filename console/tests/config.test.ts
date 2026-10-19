/*
  The console's Static Web Apps config against the live web/ config, while
  both exist: the CSP is the same policy plus Trusted Types (which makes any
  string reaching innerHTML, eval or a script URL throw in the browser), and
  every exclude pattern keeps to one '*' (Static Web Apps refuses the deploy
  otherwise, deploy run 39270419700).
*/
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { REPO } from "./helpers";

type Config = {
  navigationFallback: { rewrite: string; exclude: string[] };
  globalHeaders: Record<string, string>;
};

const consoleConfig = JSON.parse(readFileSync(join(REPO, "console", "public", "staticwebapp.config.json"), "utf8")) as Config;
const webConfig = JSON.parse(readFileSync(join(REPO, "web", "staticwebapp.config.json"), "utf8")) as Config;
const TRUSTED_TYPES = "; require-trusted-types-for 'script'; trusted-types 'none'";

describe("staticwebapp.config.json", () => {
  it("keeps the web CSP and adds only Trusted Types", () => {
    expect(consoleConfig.globalHeaders["Content-Security-Policy"]).toBe(webConfig.globalHeaders["Content-Security-Policy"] + TRUSTED_TYPES);
    const policy = consoleConfig.globalHeaders["Content-Security-Policy"];
    expect(policy).toContain("script-src 'self';");
    expect(policy).toContain("style-src 'self';");
    expect(policy).not.toContain("unsafe-inline");
    expect(policy).not.toContain("unsafe-eval");
  });

  it("keeps every other header of the web config", () => {
    for (const [name, value] of Object.entries(webConfig.globalHeaders)) {
      if (name !== "Content-Security-Policy") {
        expect(consoleConfig.globalHeaders[name], name).toBe(value);
      }
    }
  });

  it("excludes the built assets from the fallback with one wildcard per pattern", () => {
    expect(consoleConfig.navigationFallback.rewrite).toBe("/index.html");
    expect(consoleConfig.navigationFallback.exclude).toContain("/assets/*");
    for (const pattern of consoleConfig.navigationFallback.exclude) {
      expect(pattern.split("*").length - 1, pattern).toBeLessThanOrEqual(1);
    }
    for (const pattern of webConfig.navigationFallback.exclude) {
      expect(consoleConfig.navigationFallback.exclude.some((p) => p === pattern || p.startsWith(pattern.slice(0, -1))), pattern).toBe(true);
    }
  });
});
