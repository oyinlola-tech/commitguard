import { control, expect, signIn, test } from "./fixtures";

type Check = { conclusion: string | null };

/**
 * The Phase 7 lifecycle in a real browser against the real stack:
 * blocked pull request -> notification in the bell and inbox -> commit fixed ->
 * GitHub "Re-run" -> execution #2 -> merge queue validates the merge group ->
 * a problematic policy is published and rolled back -> notification and audit.
 */
test("notifications, re-runs, merge queue and policy rollback", async ({ context, page }) => {
  await signIn(context, page);
  const conclusion = async (sha: string) => (await control<Check>(page, "check", { sha })).conclusion;

  // A blocked pull request produces one notification.
  const blocked = await control<{ head: string }>(page, "phase7-commit");
  await expect.poll(() => conclusion(blocked.head), { timeout: 20_000 }).toBe("failure");
  await expect.poll(async () => (await control<{ dispatched: number }>(page, "notify")).dispatched, { timeout: 10_000 }).toBeGreaterThanOrEqual(0);
  await page.goto("/dashboard");
  const bell = page.getByRole("link", { name: /^Notifications: \d+ unread/ });
  await expect(bell).toBeVisible({ timeout: 15_000 });
  await bell.click();
  await expect(page.getByRole("heading", { name: "Notifications", level: 1 })).toBeVisible();
  const inbox = page.getByRole("list", { name: "Notifications" });
  await inbox.getByRole("link", { name: "Blocked: ai_coauthor in octo-org/payments-api" }).first().click();
  await expect(page.getByRole("heading", { name: "AI coauthor detected" })).toBeVisible();

  // The developer fixes the commit; GitHub "Re-run" creates execution #2 of that scan.
  const fixed = await control<{ head: string }>(page, "phase7-fix");
  await expect.poll(() => conclusion(fixed.head), { timeout: 20_000 }).toBe("success");
  expect((await control<{ status: string }>(page, "rerun", { sha: fixed.head })).status).toBe("queued");
  await page.goto(`/scans?q=${fixed.head}`);
  await page.getByRole("table", { name: "Scans" }).locator(".row-link").last().click();
  await expect.poll(async () => {
    await page.reload();
    const table = page.getByRole("table", { name: "Executions of this scan" });
    await expect(table).toBeVisible();
    return table.getByText("Re-run").isVisible();
  }, { timeout: 20_000 }).toBe(true);
  await expect(page.getByRole("table", { name: "Executions of this scan" }).getByText("PASS").first()).toBeVisible();

  // The pull request enters the merge queue; the merge group commit is validated.
  const group = await control<{ head: string; status: string }>(page, "merge-group");
  expect(group.status).toBe("queued");
  await expect.poll(() => conclusion(group.head), { timeout: 20_000 }).toBe("success");
  await page.goto("/repositories/5001");
  const queue = page.getByRole("region", { name: "Merge queue" });
  await expect(queue.getByText(group.head.slice(0, 7)).first()).toBeVisible({ timeout: 15_000 });
  await expect(queue.getByText("PASS").first()).toBeVisible();

  // An administrator publishes a problematic policy, then rolls it back.
  await page.goto("/policies");
  const floor = page.getByLabel("Organization floor for bot_identity");
  await floor.selectOption("block");
  await page.getByLabel("Reason for this change").fill("stricter bot policy");
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect(page.getByText(/Saved as version \d+/)).toBeVisible();
  await page.getByLabel("Organization floor for ai_identity").selectOption("warn");
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect(page.getByText(/Saved as version \d+/)).toBeVisible();

  await page.getByText("Version history and rollback").first().click();
  const rollbackButton = page.getByRole("button", { name: /^Roll back to v\d+$/ }).first();
  await expect(rollbackButton).toBeVisible();
  await rollbackButton.click();
  const dialog = page.getByRole("dialog", { name: "Roll back organization policy" });
  await expect(dialog.getByText(/This will change the effective security policy/)).toBeVisible();
  await expect(dialog.getByRole("table", { name: "Impact of the rollback" })).toBeVisible();
  const confirm = dialog.getByRole("button", { name: /^Roll back to v\d+$/ });
  await expect(confirm).toBeDisabled();
  await dialog.getByLabel(/Reason/).fill("the ai_identity floor was published by mistake");
  await dialog.getByLabel(/I understand/).check();
  await confirm.click();
  await expect(page.getByText(/Rolled back: version \d+ restores version \d+/)).toBeVisible();
  await expect(page.getByText(/Rollback · restores v\d+ · replaced v\d+/).first()).toBeVisible();

  // The rollback is audited and notified.
  await control(page, "notify");
  await page.goto("/notifications?tab=policy");
  await expect(page.getByRole("list", { name: "Notifications" }).getByText(/Organization policy rolled back/).first()).toBeVisible({ timeout: 15_000 });
  await page.goto("/audit?type=organization_policy_rolled_back");
  await expect(page.getByRole("table", { name: "Audit events" }).getByText("alice").first()).toBeVisible();
});

test("a viewer cannot roll back policy or manage notification delivery", async ({ context, page }) => {
  await signIn(context, page, 502, "/policies");
  await page.getByText("Version history and rollback").first().click();
  await expect(page.getByRole("button", { name: /Compare with v\d+/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Roll back to/ })).toHaveCount(0);
  const csrf = (await (await page.request.get("/api/v1/auth/session")).json()).data.csrf_token as string;
  const forged = await page.request.post("/api/v1/policies/1001/rollback", {
    data: { target_version: 1, expected_current_version: 2, reason: "x", confirm: true },
    headers: { "X-CSRF-Token": csrf, Origin: new URL(page.url()).origin },
  });
  expect(forged.status()).toBe(403);
  await page.goto("/settings");
  await expect(page.getByText(/managed by admins and owners/).first()).toBeVisible();
  await expect(page.getByLabel("HTTPS endpoint URL")).toHaveCount(0);
});
