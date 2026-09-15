// Capture review screenshots against a running e2e harness (not part of the test suite).
import { chromium } from "@playwright/test";

import { installGitHubSignIn } from "./github-auth.mjs";

const BASE = process.env.BASE ?? "http://localhost:4173";
const OUT = process.env.OUT;
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium" });

async function signIn(page) {
  await page.goto(`${BASE}/login`);
  await page.getByRole("link", { name: /Continue with GitHub/ }).click();
  await page.waitForURL(/\/dashboard/);
}

const pages = (process.env.PAGES ?? "/,/dashboard,/violations,/scans,/policies,/nope").split(",");
for (const scheme of ["light", "dark"]) {
  for (const [label, viewport] of [["desktop", { width: 1440, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
    const context = await browser.newContext({ viewport, colorScheme: scheme, reducedMotion: "reduce" });
    await installGitHubSignIn(context, BASE, () => 501);
    const page = await context.newPage();
    await signIn(page);
    for (const path of pages) {
      await page.goto(`${BASE}${path}`);
      await page.waitForLoadState("domcontentloaded");
      await page.waitForTimeout(1500);
      const name = path === "/" ? "landing" : path.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "");
      await page.screenshot({ path: `${OUT}/${name}-${label}-${scheme}.png`, fullPage: process.env.FULL === "1" });
    }
    await context.close();
  }
}
await browser.close();
