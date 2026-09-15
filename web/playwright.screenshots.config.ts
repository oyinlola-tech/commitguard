import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.SCREENSHOT_PORT ?? 4175);
const executablePath = process.env.CHROMIUM_PATH || undefined;

/**
 * README screenshots of the real dashboard: `npm run build && npm run screenshots`.
 * Starts the demo stack (tests/e2e/dashboard_harness.py --demo), which replays the
 * complete lifecycle through the real services, and writes PNGs to docs/images/.
 */
export default defineConfig({
  testDir: "./screenshots",
  testMatch: "*.spec.ts",
  outputDir: "test-results-screenshots",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 } }],
  webServer: {
    command: `../.venv/bin/python ../tests/e2e/dashboard_harness.py --demo --port ${PORT} --static dist`,
    url: `http://localhost:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "pipe",
  },
});
