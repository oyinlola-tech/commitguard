import { expect, test, type Page } from "@playwright/test";

const OUT = "../docs/images";

async function settle(page: Page) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(900);
}

async function signIn(page: Page, user = 501) {
  await page.goto(`/demo/sign-in?user=${user}`);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function api<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/v1${path}`);
  expect(response.ok()).toBeTruthy();
  return ((await response.json()) as { data: T }).data;
}

test("README screenshots from the demo stack", async ({ page, browser }) => {
  await page.goto("/");
  await settle(page);
  await page.screenshot({ path: `${OUT}/landing.png` });

  await signIn(page);
  await settle(page);
  await page.screenshot({ path: `${OUT}/overview.png` });

  await page.goto("/scans");
  await expect(page.getByRole("table", { name: "Scans" })).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/scans.png` });

  type Scan = { id: string; trigger: string; result: string; event: string };
  const scans = await api<Scan[]>(page, "/scans?limit=50");
  const rerun = scans.find((s) => s.trigger === "rerun");
  if (rerun) {
    await page.goto(`/scans/${rerun.id}`);
    const executions = page.locator("#executions");
    await expect(executions.getByRole("table", { name: "Executions of this scan" })).toBeVisible();
    await settle(page);
    await page.locator(".result-hero").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${OUT}/scan-executions.png`, fullPage: true });
  }

  type Violation = { id: string; status: string; rule_id: string };
  const violations = await api<Violation[]>(page, "/violations?status=open");
  const ai = violations.find((v) => v.rule_id === "ai_coauthor") ?? violations[0];
  if (ai) {
    await page.goto(`/violations/${ai.id}`);
    await expect(page.getByRole("heading", { name: "Recommended action" })).toBeVisible();
    await settle(page);
    await page.screenshot({ path: `${OUT}/violation.png` });
  }

  await page.goto("/repositories/5001");
  const queue = page.getByRole("region", { name: "Merge queue" });
  await expect(queue.getByText("ENABLED", { exact: true })).toBeVisible();
  await expect(queue.getByText("Merge queue events are not enabled")).toHaveCount(0);
  await settle(page);
  await page.screenshot({ path: `${OUT}/repository.png` });
  await queue.screenshot({ path: `${OUT}/merge-queue.png` });

  await page.goto("/policies/1001");
  await expect(page.getByText(/Rollback · restores v1/)).toBeVisible();
  await settle(page);
  await page.locator("details.disclosure").first().screenshot({ path: `${OUT}/policy-rollback.png` });

  await page.goto("/notifications");
  await expect(page.getByRole("list", { name: "Notifications" })).toBeVisible();
  await settle(page);
  await page.screenshot({ path: `${OUT}/notifications.png` });

  await page.goto("/settings");
  const settings = page.locator("#notifications");
  await expect(settings.getByRole("table").first()).toBeVisible();
  await settle(page);
  await settings.screenshot({ path: `${OUT}/notification-settings.png` });

  const dark = await browser.newContext({ colorScheme: "dark", viewport: { width: 1280, height: 800 }, reducedMotion: "reduce" });
  const night = await dark.newPage();
  await signIn(night, 504);
  await settle(night);
  await night.screenshot({ path: `${OUT}/overview-dark.png` });
  await dark.close();
});
