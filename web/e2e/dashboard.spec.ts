import { control, expect, signIn, test } from "./fixtures";

/**
 * The primary Phase 6 scenario, in a real browser against the real stack:
 * sign in -> repositories available -> clean scan PASS -> AI-attributed commit
 * -> webhook -> scan -> GitHub check fails -> violation with remediation ->
 * commit corrected -> new scan PASS -> violation resolved, history kept.
 */
test("a blocked commit is detected, explained, fixed and kept in history", async ({ context, page }) => {
  await signIn(context, page);

  // Connected GitHub installation and synchronized repositories.
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Installations" }).click();
  await expect(page.getByRole("heading", { name: "Installations" })).toBeVisible();
  await page.getByRole("link", { name: "octo-org", exact: true }).click();
  await page.getByRole("button", { name: "Sync repositories" }).click();
  await expect(page.getByText("Repositories synchronized", { exact: true })).toBeVisible();
  await page.goto("/repositories");
  await expect(page.getByRole("link", { name: /payments-api/ })).toBeVisible();

  // The clean pull request from the seed passed.
  await page.goto("/scans?result=pass");
  await expect(page.getByRole("table", { name: "Scans" }).getByText("PASS").first()).toBeVisible();

  // An AI-attributed commit arrives through a signed webhook and is scanned.
  const ai = await control<{ head: string }>(page, "ai-commit");
  await page.goto("/scans");
  await expect(page.getByRole("table", { name: "Scans" }).getByText("BLOCKED")).toBeVisible({ timeout: 20_000 });
  await expect.poll(async () => (await control<{ conclusion: string | null }>(page, "check", { sha: ai.head })).conclusion).toBe("failure");

  // The violation appears; its detail explains the evidence and the fix.
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: /Violations/ }).click();
  await page.getByRole("link", { name: /AI coauthor detected/ }).click();
  await expect(page.getByRole("heading", { name: "AI coauthor detected" })).toBeVisible();
  await expect(page.getByText("Claude <noreply@anthropic.com>")).toBeVisible();
  await expect(page.getByText(/Remove the Co-authored-by line/)).toBeVisible();
  await expect(page.getByText("CommitGuard does not rewrite Git history or modify commits automatically.")).toBeVisible();
  await expect(page.getByText(/Detected in pull request #9/)).toBeVisible();
  const violationUrl = page.url();

  // The developer corrects the commit; the new scan passes and the dashboard updates.
  await control(page, "fix-commit");
  await expect.poll(async () => {
    await page.reload();
    await expect(page.getByRole("heading", { name: "Recommended action" })).toBeVisible();
    return page.getByText("No longer present", { exact: true }).isVisible();
  }, { timeout: 20_000 }).toBe(true);
  await expect(page.getByText(/no longer part of pull request #9/).first()).toBeVisible();

  // History remains auditable: the blocked detection and the audit trail.
  await expect(page.getByRole("table", { name: /Scans that detected/ }).getByText("BLOCKED")).toBeVisible();
  await page.goto("/audit?type=violation_resolved");
  await expect(page.getByRole("table", { name: "Audit events" }).getByText(/Violation resolved: no longer part of pull request #9/).first()).toBeVisible();
  await page.goto(violationUrl);
  await expect(page.getByText("RESOLVED").first()).toBeVisible();
});

test("a viewer can read but not change policy, and deep links survive a refresh", async ({ context, page }) => {
  await signIn(context, page, 502, "/policies");
  await expect(page.getByText(/Your role can view this policy/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Save policy" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Audit log" })).toHaveCount(0);
  await page.goto("/rules/ai_coauthor");
  await page.reload();
  await expect(page.getByRole("heading", { name: "AI co-author attribution" })).toBeVisible();
});

test("signing out ends the session on the server", async ({ context, page }) => {
  await signIn(context, page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/login\?reason=signed_out/);
  await expect(page.getByText("You have signed out.")).toBeVisible();
  const response = await page.request.get("/api/v1/auth/session");
  expect(response.status()).toBe(401);
  await page.goto("/violations");
  await page.waitForURL(/\/login\?return_to=%2Fviolations/);
});
