// Review screenshots of detail pages against a running e2e harness (not part of the test suite).
import { chromium } from "@playwright/test";

import { installGitHubSignIn } from "./github-auth.mjs";

const BASE = process.env.BASE ?? "http://localhost:4173";
const OUT = process.env.OUT;
const scheme = process.env.SCHEME ?? "light";
const width = Number(process.env.WIDTH ?? 1440);
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium" });
const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme, reducedMotion: "reduce" });
await installGitHubSignIn(context, BASE, () => 501);
const page = await context.newPage();
const shot = async (name, full = true) => {
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}-${width}-${scheme}.png`, fullPage: full });
};

await page.goto(`${BASE}/`);
await shot("landing-full");
await page.goto(`${BASE}/login?reason=expired`);
await shot("login", false);
await page.getByRole("link", { name: /Continue with GitHub/ }).click();
await page.waitForURL(/\/dashboard/);
await page.goto(`${BASE}/violations`);
await page.waitForTimeout(800);
await page.getByRole("link", { name: /AI coauthor detected/ }).first().click();
await shot("violation-detail");
await page.goto(`${BASE}/scans?result=blocked`);
await page.waitForTimeout(800);
await page.locator(".row-link").first().click();
await shot("scan-detail");
await page.goto(`${BASE}/policies`);
await shot("policies");
await page.goto(`${BASE}/repositories`);
await page.waitForTimeout(800);
await page.locator(".row-link").first().click();
await shot("repository-detail");
await page.goto(`${BASE}/definitely/not/here`);
await shot("not-found", false);
await browser.close();
