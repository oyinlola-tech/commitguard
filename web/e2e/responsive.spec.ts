import { expect, signIn, test } from "./fixtures";

const VIEWPORTS = [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 820, height: 1180 },
  { name: "desktop", width: 1440, height: 900 },
];
const PAGES = ["/", "/login", "/dashboard", "/repositories", "/repositories/5001", "/scans", "/violations", "/policies", "/rules", "/audit", "/github/installations", "/notifications", "/settings", "/missing"];

for (const viewport of VIEWPORTS) {
  test(`no horizontal overflow at ${viewport.name} (${viewport.width}px)`, async ({ context, page }) => {
    await page.setViewportSize(viewport);
    await signIn(context, page);
    for (const path of PAGES) {
      await page.goto(path);
      await page.waitForTimeout(400);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, path).toBeLessThanOrEqual(1);
    }
  });
}

test("mobile uses a navigation drawer and stacked table cards", async ({ context, page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(context, page, 501, "/scans");
  await expect(page.locator(".sidebar")).toBeHidden();
  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  const header = page.locator("table.table thead").first();
  await expect(header).toHaveCSS("position", "absolute");
  await expect(page.getByRole("button", { name: /Filters/ })).toBeVisible();
});

test("desktop shows the sidebar and a full table", async ({ context, page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(context, page, 501, "/scans");
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeHidden();
  await expect(page.getByRole("columnheader", { name: "Duration" })).toBeVisible();
});
