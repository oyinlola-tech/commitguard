import AxeBuilder from "@axe-core/playwright";

import { expect, signIn, test } from "./fixtures";

const PAGES = ["/dashboard", "/repositories", "/scans", "/violations", "/policies", "/rules", "/rules/ai_coauthor", "/audit", "/github/installations", "/settings", "/missing"];

async function audit(page: import("@playwright/test").Page, label: string) {
  await page.waitForTimeout(600);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious, label).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test(`WCAG 2.2 AA checks pass in ${scheme} mode, including contrast`, async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: scheme, reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto("/");
    await audit(page, `/ ${scheme}`);
    await page.goto("/login");
    await audit(page, `/login ${scheme}`);
    await signIn(context, page);
    for (const path of PAGES) {
      await page.goto(path);
      await audit(page, `${path} ${scheme}`);
    }
    await page.goto("/violations");
    await page.locator(".row-link").first().click();
    await audit(page, `violation detail ${scheme}`);
    await page.goto("/scans");
    await page.locator(".row-link").first().click();
    await audit(page, `scan detail ${scheme}`);
    await context.close();
  });
}

test("keyboard users can skip to content and reach every navigation item", async ({ context, page }) => {
  await signIn(context, page);
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  await expect(skip).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#main")).toBeFocused();
  const nav = page.getByRole("navigation", { name: "Primary" });
  await nav.getByRole("link", { name: "Violations" }).focus();
  const outline = await nav.getByRole("link", { name: "Violations" }).evaluate((el) => getComputedStyle(el).outlineStyle);
  expect(outline).not.toBe("none");
});
