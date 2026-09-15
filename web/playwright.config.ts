import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 4174);
const executablePath = process.env.CHROMIUM_PATH || undefined;

/**
 * Browser tests against the complete stack started by tests/e2e/dashboard_harness.py:
 * GitHub App service, workers, real Git, SQLite, dashboard API and the built dashboard.
 * Run `npm run build` first. Tests share one backend and run in order.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `../.venv/bin/python ../tests/e2e/dashboard_harness.py --port ${PORT} --seed --static dist`,
    url: `http://localhost:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: "pipe",
  },
});
