import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, OWNER, signIn, test, VIEWER } from "./fixtures";

/**
 * Phase 8 organization governance in a real browser against the seeded stack:
 * octo-org with the groups Production and Documentation, a pending organization
 * draft by alice, two exceptions and a scan schedule.
 */

async function audit(page: Page, label: string) {
  await page.waitForTimeout(600);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  expect(serious, label).toEqual([]);
}

async function api<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`/api/v1${path}`);
  expect(response.ok(), path).toBeTruthy();
  return ((await response.json()) as { data: T }).data;
}

async function seededIds(page: Page) {
  const groups = await api<{ id: string; name: string }[]>(page, "/organizations/1001/repository-groups");
  const drafts = await api<{ id: string; title: string }[]>(page, "/organizations/1001/policy-drafts?state=pending_approval");
  const exceptions = await api<{ id: string; rule_id: string }[]>(page, "/organizations/1001/exceptions?status=active");
  const found = (id: string | undefined, what: string): string => {
    expect(id, `seeded ${what}`).toBeTruthy();
    return id ?? "";
  };
  return {
    production: found(groups.find((g) => g.name === "Production")?.id, "group Production"),
    draft: found(drafts.find((d) => d.title === "Block AI attribution trailers everywhere")?.id, "pending draft"),
    exception: found(exceptions.find((e) => e.rule_id === "malformed_trailer")?.id, "active exception"),
  };
}

test("the command center shows the organization posture with reasons and compliance", async ({ context, page }) => {
  await signIn(context, page, OWNER, "/organization");
  await expect(page.getByRole("heading", { name: "Command center", level: 1 })).toBeVisible();
  const posture = page.getByRole("region", { name: "Organization posture" });
  await expect(posture.getByText(/SECURE|AT RISK|UNPROTECTED|UNKNOWN/).first()).toBeVisible();
  await expect(posture.getByRole("listitem").first()).toBeVisible();
  await expect(page.getByText(/^\d+ of \d+ required repositories satisfy all mandatory controls$/)).toBeVisible();
  await expect(page.getByText(/Updated/).first()).toBeVisible();
  await expect(page.getByRole("region", { name: "GitHub installations" }).getByRole("link", { name: "octo-org" })).toBeVisible();
});

test("the matrix filters by group and a bulk operation adds a repository to a new group", async ({ context, page }) => {
  test.setTimeout(180_000);
  await signIn(context, page, OWNER, "/organization/repositories");
  const { production } = await seededIds(page);
  await page.getByLabel("Group", { exact: true }).selectOption(production);
  await expect(page).toHaveURL(new RegExp(`group=${production}`));
  const table = page.getByRole("table", { name: "Repository security matrix" });
  await expect(table.getByRole("link", { name: "octo-org/payments-api" })).toBeVisible();
  await expect(table.getByRole("link", { name: "octo-org/web-console" })).toBeVisible();
  await expect(table.getByRole("link", { name: "octo-org/engineering-handbook" })).toHaveCount(0);

  await page.getByRole("checkbox", { name: "Select octo-org/payments-api" }).check();
  const bar = page.getByRole("region", { name: "Bulk actions" });
  await expect(bar.getByText("1 repository selected")).toBeVisible();
  await bar.getByRole("button", { name: "Add to group" }).click();
  const dialog = page.getByRole("dialog", { name: "Add repositories to a group" });
  await dialog.getByLabel("Or create a group").fill("E2E pilot");
  await dialog.getByRole("button", { name: "Create group" }).click();
  await expect(dialog.getByLabel("Repository group")).not.toHaveValue("");
  await dialog.getByRole("button", { name: /Add repositories to a group \(1 repository\)/ }).click();

  const progress = page.getByRole("region", { name: "Bulk operation", exact: true });
  await expect(progress.getByText(/Add to group · 1 repository/)).toBeVisible();
  // The harness applies bulk operations in its maintenance loop (every 60 s); the page polls with backoff.
  await expect(progress.getByText("COMPLETED: 1 completed · 0 failed · 0 skipped · 0 pending", { exact: true })).toBeVisible({ timeout: 150_000 });
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(table.getByRole("link", { name: "E2E pilot" })).toBeVisible({ timeout: 15_000 });
});

test("the author of a pending policy change sees separation of duties and cannot approve it", async ({ context, page }) => {
  await signIn(context, page, OWNER, "/organization/policies");
  await page.getByRole("link", { name: "Block AI attribution trailers everywhere" }).click();
  await expect(page.getByRole("heading", { name: "Block AI attribution trailers everywhere", level: 1 })).toBeVisible();
  await expect(page.getByText("PENDING APPROVAL").first()).toBeVisible();
  await expect(page.getByText(/Separation of duties: the author of a policy change cannot approve it\./)).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("table", { name: "Changes from the current version" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run simulation" })).toBeVisible();
});

test("exceptions are listed and an exception that needs no approval becomes active", async ({ context, page }) => {
  await signIn(context, page, OWNER, "/organization/exceptions");
  const table = page.getByRole("table", { name: "Policy exceptions" });
  await expect(table.getByText("octo-org/engineering-handbook")).toBeVisible();
  await expect(table.getByText("octo-org/web-console")).toBeVisible();

  await page.getByRole("link", { name: "Request an exception" }).click();
  await expect(page.getByRole("heading", { name: "Request an exception", level: 1 })).toBeVisible();
  await page.getByLabel("Rule", { exact: true }).selectOption("ai_coauthor");
  await expect(page.getByText("Approval will be needed")).toBeVisible();
  await page.getByLabel("Rule", { exact: true }).selectOption("malformed_trailer");
  await page.getByRole("combobox", { name: "Repository" }).selectOption({ label: "octo-org/payments-api" });
  await expect(page.getByText("No approval expected")).toBeVisible();
  await page.getByRole("radio", { name: /ALLOW/ }).check();
  await page.getByLabel(/Justification/).fill("Release tooling writes non-standard trailers");
  await page.getByRole("button", { name: "Request exception" }).click();
  await expect(page.getByRole("heading", { name: "Malformed trailer exception", level: 1 })).toBeVisible();
  await expect(page.getByText("ACTIVE").first()).toBeVisible();
  await expect(page.getByRole("region", { name: "Lifecycle" }).getByText("Requested by alice")).toBeVisible();
});

test("a viewer sees governance pages without write actions", async ({ context, page }) => {
  await signIn(context, page, VIEWER, "/organization/repositories");
  await expect(page.getByRole("table", { name: "Repository security matrix" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /^Select/ })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Add repositories" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create group" })).toHaveCount(0);
  const { draft } = await seededIds(page);

  await page.goto(`/organization/policies/drafts/${draft}`);
  await expect(page.getByRole("heading", { name: "Block AI attribution trailers everywhere", level: 1 })).toBeVisible();
  for (const name of ["Approve", "Publish", "Emergency publish", "Cancel draft", "Run simulation"]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }
  await page.goto("/organization/exceptions");
  await expect(page.getByRole("table", { name: "Policy exceptions" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Request an exception" })).toHaveCount(0);
  await page.goto("/settings/organization");
  await expect(page.getByText("Your role can view these settings.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Save settings/ })).toHaveCount(0);
  await expect(page.getByLabel(/Longest exception/)).toBeDisabled();

  const csrf = (await (await page.request.get("/api/v1/auth/session")).json()).data.csrf_token as string;
  const forged = await page.request.post("/api/v1/organizations/1001/repository-groups", {
    data: { name: "forged", description: null },
    headers: { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
  });
  expect(forged.status()).toBe(403);
});

for (const scheme of ["light", "dark"] as const) {
  test(`organization governance pages pass WCAG 2.2 AA checks in ${scheme} mode`, async ({ browser }) => {
    test.setTimeout(120_000);
    const context = await browser.newContext({ colorScheme: scheme, reducedMotion: "reduce" });
    const page = await context.newPage();
    await signIn(context, page, OWNER, "/organization");
    const ids = await seededIds(page);
    const pages = [
      "/organization",
      "/organization/repositories",
      "/organization/repositories/add",
      `/organization/groups/${ids.production}`,
      "/organization/policies",
      "/organization/policies/drafts/new",
      `/organization/policies/drafts/${ids.draft}`,
      "/organization/exceptions",
      "/organization/exceptions/new",
      `/organization/exceptions/${ids.exception}`,
      "/organization/security",
      "/organization/audit",
      "/settings/organization",
      "/settings/organization/members",
      "/repositories/5003",
    ];
    for (const path of pages) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await audit(page, `${path} ${scheme}`);
    }
    await context.close();
  });
}

test("the repository matrix and governance pages fit a 375 px screen", async ({ context, page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await signIn(context, page, OWNER, "/organization/repositories");
  await expect(page.getByRole("table", { name: "Repository security matrix" })).toBeVisible();
  await expect(page.locator("table.matrix thead")).toHaveCSS("position", "absolute");
  const ids = await seededIds(page);
  for (const path of ["/organization/repositories", "/organization", "/organization/policies", `/organization/policies/drafts/${ids.draft}`, "/organization/exceptions", "/organization/security", "/settings/organization", "/repositories/5003"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(400);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, path).toBeLessThanOrEqual(1);
  }
});
