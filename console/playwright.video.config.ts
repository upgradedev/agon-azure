/*
  Submission video capture only (e2e/video-capture.ts), run by the video
  workflows against the live site. It starts no local server, and the default
  playwright.config.ts never matches the capture file. One worker, serial, no
  retries: a clip is recorded once or the run fails with a trace.
*/
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  testMatch: /video-capture\.ts$/,
  outputDir: "test-results/video",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 180_000,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report/video" }]],
  use: {
    browserName: "chromium",
    trace: "retain-on-failure",
  },
});
