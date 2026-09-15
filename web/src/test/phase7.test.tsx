import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import * as f from "./fixtures";
import { apiError, data, mockFetch, page, renderApp } from "./render";

describe("notification center", () => {
  it("lists notifications with severity text, links and unread counts", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notifications/counts", data({ unread: 2, unread_critical: 1, capped: false }));
    api.on("GET", "/api/v1/notifications", {
      body: {
        data: [
          f.notification({ id: "1".repeat(32), severity: "critical", type: "critical_violation", title: "Blocked: custom_rule in octo-org/payments-api" }),
          f.notification({ id: "2".repeat(32), state: "read", title: "Organization policy rolled back: v11 → v12 (restores v10)", type: "policy_rolled_back", category: "policy", severity: "high", repository: null, resource_type: "policy", resource_id: "1001", link: "/policies/1001" }),
        ],
        meta: { next_cursor: null, limit: 25, counts: { unread: 1, unread_critical: 1, capped: false } },
      },
    });
    renderApp("/notifications");
    const list = await screen.findByRole("list", { name: "Notifications" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByText("CRITICAL")).toBeInTheDocument();
    expect(within(items[0]!).getByRole("link", { name: /custom_rule/ })).toHaveAttribute("href", `/violations/${"b".repeat(32)}`);
    expect(within(items[1]!).getByRole("link", { name: /rolled back/ })).toHaveAttribute("href", "/policies/1001");
    const bell = await screen.findByRole("link", { name: "Notifications: 2 unread, 1 critical" });
    expect(bell).toHaveAttribute("href", "/notifications");
  });

  it("marks a notification read and archives it through the API", async () => {
    const api = mockFetch();
    const id = "3".repeat(32);
    api.on("GET", "/api/v1/notifications", page([f.notification({ id })]));
    api.on("POST", `/api/v1/notifications/${id}/read`, data(f.notification({ id, state: "read" })));
    api.on("POST", `/api/v1/notifications/${id}/archive`, data(f.notification({ id, state: "archived" })));
    renderApp("/notifications");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Mark as read: Blocked/ }));
    await user.click(screen.getByRole("button", { name: /Archive: Blocked/ }));
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith("/archive"))).toBe(true));
    const read = api.calls.find((c) => c.path.endsWith("/read"));
    expect(read?.method).toBe("POST");
    expect(read?.headers.get("X-CSRF-Token")).toBe("c".repeat(64));
  });

  it("filters by tab through the URL and shows a proper empty state", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notifications", page([]));
    renderApp("/notifications?tab=critical");
    expect(await screen.findByText("No critical notifications")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Critical" })).toHaveAttribute("aria-pressed", "true");
    const call = api.calls.find((c) => c.path === "/api/v1/notifications");
    expect(call?.search).toContain("category=critical");
    await userEvent.setup().click(screen.getByRole("button", { name: "Archived" }));
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/v1/notifications" && c.search.includes("state=archived"))).toBe(true));
  });

  it("shows an error state with retry when notifications cannot load", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notifications", apiError(500, "INTERNAL_ERROR", "Something went wrong. Try again."));
    renderApp("/notifications");
    expect(await screen.findByText("We could not load notifications.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });

  it("never turns a notification link into an external or script URL", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notifications", page([f.notification({ link: "javascript:alert(1)", title: "<img src=x onerror=alert(1)>" })]));
    renderApp("/notifications");
    const title = await screen.findByText("<img src=x onerror=alert(1)>");
    expect(title.tagName).not.toBe("A");
    expect(document.querySelector("img")).toBeNull();
  });
});

describe("policy rollback", () => {
  function setup(role: "viewer" | "admin" = "admin") {
    const api = mockFetch(f.session(role));
    api.on("GET", "/api/v1/policies", data([f.policy(role === "admin")]));
    api.on("GET", "/api/v1/policies/1001/versions", page([
      f.policyVersion({ version: 3, status: "active", kind: "rollback", rollback_of: 2, restored_version: 1, summary: "bot_identity: warn -> repository", reason: "restore" }),
      f.policyVersion({ version: 2, summary: "bot_identity: repository -> warn" }),
    ]));
    api.on("GET", "/api/v1/policies/1001/diff", data({ from_version: 3, to_version: 2, added: [{ policy_id: "bot_identity", old: null, new: "warn", weakening: false }], changed: [], removed: [], weakening: false }));
    return api;
  }

  it("shows lineage and requires a reason and confirmation before rolling back", async () => {
    const api = setup();
    api.on("POST", "/api/v1/policies/1001/rollback", data(f.policy(true), { rollback: { new_version: 4, restored_version: 2, rollback_of: 3 } }));
    renderApp("/policies/1001");
    expect(await screen.findByText("Rollback · restores v1 · replaced v2")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Roll back to v2" }));
    const dialog = await screen.findByRole("dialog", { name: "Roll back organization policy" });
    expect(within(dialog).getByText(/This will change the effective security policy/)).toBeInTheDocument();
    expect(await within(dialog).findByRole("table", { name: "Impact of the rollback" })).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Roll back to v2" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "v3 broke bot identities");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByLabelText(/I understand/));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/v1/policies/1001/rollback")).toBe(true));
    const call = api.calls.find((c) => c.path === "/api/v1/policies/1001/rollback");
    expect(call?.body).toEqual({ target_version: 2, expected_current_version: 3, reason: "v3 broke bot identities", confirm: true });
    expect(await screen.findByText(/version 4 restores version 2/)).toBeInTheDocument();
  });

  it("explains a concurrent change instead of overwriting it", async () => {
    const api = setup();
    api.on("POST", "/api/v1/policies/1001/rollback", apiError(409, "CONFLICT", "The policy was changed by someone else (now version 4)."));
    renderApp("/policies/1001");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Roll back to v2" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Reason/), "x");
    await user.click(within(dialog).getByLabelText(/I understand/));
    await user.click(within(dialog).getByRole("button", { name: "Roll back to v2" }));
    expect(await within(dialog).findByText("The policy changed")).toBeInTheDocument();
    expect(within(dialog).getByText(/now version 4/)).toBeInTheDocument();
  });

  it("does not offer rollback without policies:rollback and compares versions for everyone", async () => {
    setup("viewer");
    renderApp("/policies/1001");
    expect(await screen.findByText("Rollback · restores v1 · replaced v2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Roll back/ })).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Compare with v3" }));
    expect(await screen.findByRole("table", { name: "Differences from v3 to v2" })).toBeInTheDocument();
  });
});

describe("executions, merge queue and enforcement risk", () => {
  it("shows every execution and warns when policy versions differ", async () => {
    const api = mockFetch();
    const scanId = "a".repeat(32);
    api.on("GET", `/api/v1/scans/${scanId}`, data(f.scanDetail({ executions: 2, latest_execution: "9".repeat(32) }, { result: "blocked" })));
    api.on("GET", `/api/v1/scans/${scanId}/executions`, data({
      scan_id: scanId,
      items: [
        f.execution({ id: "9".repeat(32), execution: 2, trigger: "rerun", result: "pass", organization_policy_version: 2, current: true }),
        f.execution({ id: scanId, execution: 1, trigger: "pull_request", result: "blocked", organization_policy_version: 1 }),
      ],
      policy_changed: true,
      rules_changed: false,
    }));
    renderApp(`/scans/${scanId}`);
    const table = await screen.findByRole("table", { name: "Executions of this scan" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[1]!).getByText("Re-run")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("PASS")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("BLOCKED")).toBeInTheDocument();
    expect(screen.getByText("Executions used different policy versions")).toBeInTheDocument();
    expect(screen.getByText("Pull request · execution #1 of 2")).toBeInTheDocument();
  });

  it("shows a merge group result as a merge queue failure, not a pull request failure", async () => {
    const api = mockFetch();
    const scanId = "a".repeat(32);
    api.on("GET", `/api/v1/scans/${scanId}`, data(f.scanDetail({
      merge_group: { head_sha: f.SHA, base_sha: f.BASE_SHA, base_ref: "refs/heads/main", pull_requests: [7, 9], state: "checks_requested", destroyed_reason: null, result: "blocked", scan: scanId, created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z", validated_at: null },
    }, { event: "merge_group", trigger: "merge_group", pull_request_number: null, failure_source: "merge_queue" })));
    api.on("GET", `/api/v1/scans/${scanId}/executions`, data({ scan_id: scanId, items: [f.execution()], policy_changed: false, rules_changed: false }));
    renderApp(`/scans/${scanId}`);
    expect(await screen.findByRole("heading", { name: "Merge queue" })).toBeInTheDocument();
    expect(screen.getByText("#7, #9")).toBeInTheDocument();
    expect(screen.getByText(/Failure source: merge queue/)).toBeInTheDocument();
    expect(screen.getAllByText(/Merge queue for main/).length).toBeGreaterThan(0);
  });

  it("never presents an unknown merge queue as disabled and flags a lost installation", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/5001", data(f.repositoryDetail({ repository: f.repository({ protection: "at_risk", protection_reason: "The GitHub App installation is suspended: CommitGuard checks no longer run." }) })));
    api.on("GET", "/api/v1/repositories/5001/merge-queue", data({ repository_id: 5001, status: "unknown", detail: "main is protected; whether classic protection requires a merge queue is not visible", checked_at: null, permission: "missing", current: null, recent: [] }));
    renderApp("/repositories/5001");
    expect(await screen.findByText("AT RISK")).toBeInTheDocument();
    const panel = await screen.findByRole("region", { name: "Merge queue" });
    expect(await within(panel).findByText("UNKNOWN")).toBeInTheDocument();
    expect(within(panel).queryByText("NOT ENABLED")).toBeNull();
    expect(within(panel).getByText("Merge queue events are not enabled")).toBeInTheDocument();
  });

  it("shows an unavailable merge queue status as an error, not as disabled", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/5001", data(f.repositoryDetail()));
    api.on("GET", "/api/v1/repositories/5001/merge-queue", apiError(502, "GITHUB_UNAVAILABLE", "GitHub could not be reached."));
    renderApp("/repositories/5001");
    expect(await screen.findByText("Merge queue status unavailable.")).toBeInTheDocument();
  });

  it("warns on the overview when GitHub enforcement is at risk", async () => {
    const api = mockFetch();
    const view = f.overview({ repositories_at_risk: 2 });
    view.integration = { ...view.integration, status: "disconnected", detail: "The GitHub App was uninstalled." };
    api.on("GET", "/api/v1/dashboard/overview", data(view));
    renderApp("/dashboard");
    expect(await screen.findByText("GitHub enforcement at risk")).toBeInTheDocument();
    expect(screen.getByText(/2 repositories are no longer checked/)).toBeInTheDocument();
  });

  it("labels superseded executions as stale", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/scans", page([f.scan({ result: "stale" })]));
    renderApp("/scans");
    expect(await screen.findByText("STALE")).toBeInTheDocument();
  });
});

describe("notification settings", () => {
  it("locks mandatory types and saves personal mutes for the member's own inbox", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", "/api/v1/notification-preferences", data([f.notificationSettings(false)]));
    api.on("GET", "/api/v1/auth/sessions", data([]));
    api.on("PATCH", "/api/v1/notification-preferences", data(f.notificationSettings(false)));
    renderApp("/settings");
    const table = await screen.findByRole("table", { name: "Notification types for octo-org" });
    expect(within(table).getByText("Always")).toBeInTheDocument();
    expect(within(table).queryByRole("checkbox", { name: "E-mail for High-severity violations" })).toBeNull();
    await userEvent.setup().click(within(table).getByRole("checkbox", { name: "Show High-severity violations in my inbox" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ organization_id: 1001, in_app: { high_violation: false } });
    expect(screen.getByText(/managed by admins and owners/)).toBeInTheDocument();
  });

  it("asks for confirmation before an administrator turns deliveries off", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notification-preferences", data([f.notificationSettings(true)]));
    api.on("GET", "/api/v1/auth/sessions", data([]));
    api.on("GET", "/api/v1/organizations/1001/members", page([]));
    api.on("GET", "/api/v1/organizations/1001/notification-deliveries", page([]));
    api.on("PUT", "/api/v1/organizations/1001/notification-settings", data(f.notificationSettings(true, { version: 2 })));
    renderApp("/settings");
    const user = userEvent.setup();
    const table = await screen.findByRole("table", { name: "Notification types for octo-org" });
    await user.click(within(table).getByRole("checkbox", { name: "E-mail for Critical violations" }));
    await user.click(screen.getByRole("button", { name: "Save organization settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Turn off notification deliveries?" });
    expect(within(dialog).getByText("Critical violations: E-mail")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Turn off deliveries" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PUT")).toBe(true));
    const body = api.calls.find((c) => c.method === "PUT")?.body as { confirm: boolean; expected_version: number };
    expect(body.confirm).toBe(true);
    expect(body.expected_version).toBe(1);
  });

  it("shows a new webhook's signing secret once, after explicit confirmation", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/notification-preferences", data([f.notificationSettings(true, { webhooks: [] })]));
    api.on("GET", "/api/v1/auth/sessions", data([]));
    api.on("GET", "/api/v1/organizations/1001/members", page([]));
    api.on("GET", "/api/v1/organizations/1001/notification-deliveries", page([]));
    api.on("POST", "/api/v1/organizations/1001/notification-webhooks", {
      status: 201,
      body: { data: { endpoint: { id: "d".repeat(32), url: "https://hooks.example.com/cg", created_at: "2026-09-01T12:00:00Z", created_by: "alice" }, signing_secret: "whsec_" + "f".repeat(64) }, meta: {} },
    });
    renderApp("/settings");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("HTTPS endpoint URL"), "https://hooks.example.com/cg");
    await user.click(screen.getByRole("button", { name: /Add webhook/ }));
    const dialog = await screen.findByRole("dialog", { name: "Send security notifications to this endpoint?" });
    await user.click(within(dialog).getByRole("button", { name: "Add webhook" }));
    expect(await screen.findByTestId("webhook-secret")).toHaveTextContent("whsec_");
    expect(api.calls.find((c) => c.method === "POST" && c.path.endsWith("notification-webhooks"))?.body).toEqual({ url: "https://hooks.example.com/cg", confirm: true });
    await user.click(screen.getByRole("button", { name: "I saved the secret" }));
    expect(screen.queryByTestId("webhook-secret")).toBeNull();
  });
});
