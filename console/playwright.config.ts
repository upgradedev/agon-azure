/*
  End-to-end tests, Chromium only, run in CI against the built console served
  by scripts/serve.mjs with the headers from staticwebapp.config.json, so the
  strict CSP is live in the browser during every test.

  Two projects: chromium runs every spec against dist (web/runs as committed);
  recorded runs the judge path and recorded.spec.ts against dist-recorded, the
  console workflow's rehearsal build with the recorded-run fixture listed
  first, so the site a recorded run makes is tested before one is committed.
*/
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  workers: 4,
  timeout: 60_000,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /recorded\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4173" },
    },
    {
      name: "recorded",
      testMatch: /(judge-path|recorded)\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:4174" },
    },
  ],
  webServer: [
    {
      command: "node scripts/serve.mjs dist 4173",
      url: "http://127.0.0.1:4173/",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "node scripts/serve.mjs dist-recorded 4174",
      url: "http://127.0.0.1:4174/",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
