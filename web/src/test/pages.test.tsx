import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import * as f from "./fixtures";
import { apiError, data, mockFetch, page, renderApp } from "./render";

const location = () => screen.getByTestId("location").textContent;

describe("session and navigation", () => {
  it("redirects to sign-in with a return path when there is no session", async () => {
    mockFetch(null);
    renderApp("/violations?status=open");
    await waitFor(() => expect(location()).toBe("/login?return_to=%2Fviolations%3Fstatus%3Dopen"));
    expect(screen.getByRole("heading", { name: "Sign in to CommitGuard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Continue with GitHub/ })).toHaveAttribute("href", "/api/v1/auth/login?return_to=%2Fviolations%3Fstatus%3Dopen");
  });

  it("sends the user to sign-in once when the session expires mid-use", async () => {
    const api = mockFetch();
    let expired = false;
    api.on("GET", "/api/v1/scans", () => {
      expired = true;
      return apiError(401, "SESSION_EXPIRED", "Your session has expired.");
    });
    api.on("GET", "/api/v1/auth/session", () => (expired ? apiError(401, "SESSION_EXPIRED") : data(f.session())));
    renderApp("/scans");
    await waitFor(() => expect(location()).toBe("/login?return_to=%2Fscans&reason=expired"));
    expect(await screen.findByText("Your session expired. Sign in again to continue.")).toBeInTheDocument();
    expect(api.calls.filter((c) => c.path === "/api/v1/scans")).toHaveLength(1);
  });

  it("shows only the sections the role can use and marks the current page", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", "/api/v1/scans", page([]));
    renderApp("/scans");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Scans" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link", { name: "Audit log" })).toBeNull();
    const admin = mockFetch(f.session("admin"));
    admin.on("GET", "/api/v1/scans", page([]));
  });

  it("shows the typographic 404 for unknown routes without revealing anything", async () => {
    mockFetch();
    renderApp("/no/such/page");
    expect(await screen.findByRole("heading", { name: "404 Not Found" })).toBeInTheDocument();
    expect(screen.getByText(/unknown revision or path not in the working tree/)).toBeInTheDocument();
    expect(screen.getByText(/not available to your account/)).toBeInTheDocument();
  });

  it("renders the landing page for signed-out visitors", async () => {
    mockFetch(null);
    renderApp("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Every commit makes a claim about who wrote it." })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Sign in/ }).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "What CommitGuard will never do" })).toBeInTheDocument();
  });
});

describe("overview", () => {
  it("loads, then shows server metrics exactly", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/dashboard/overview", data(f.overview({ critical_open: 2, scans_blocked: 7 })));
    renderApp("/dashboard");
    expect(await screen.findByText("Loading overview…")).toBeInTheDocument();
    expect(await screen.findByText(/CRITICAL: 2 open critical violations/)).toBeInTheDocument();
    const blocked = screen.getByText("Blocked scans").closest(".metric")!;
    expect(blocked).toHaveTextContent("7");
    expect(screen.getByText("Merge protection")).toBeInTheDocument();
  });

  it("shows an error state with retry", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/dashboard/overview", apiError(500, "INTERNAL_ERROR", "Something went wrong. Try again."));
    renderApp("/dashboard");
    expect(await screen.findByText("We could not load the overview.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });

  it("explains empty access", async () => {
    mockFetch(f.session("admin", []));
    renderApp("/dashboard");
    expect(await screen.findByText("You do not have access to an organization yet.")).toBeInTheDocument();
  });

  it("shows empty recent activity honestly", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/dashboard/overview", data({ ...f.overview(), recent_scans: [], recent_violations: [], repository_health: [] }));
    renderApp("/dashboard");
    expect(await screen.findByText("No scans yet.")).toBeInTheDocument();
    expect(screen.getByText(/Connect a GitHub repository to begin monitoring/)).toBeInTheDocument();
  });
});

describe("repositories", () => {
  it("lists repositories with protection as text", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories", page([f.repository(), f.repository({ id: 5002, name: "web", full_name: "octo-org/web", protection: "protected", last_scan: null })]));
    renderApp("/repositories");
    const table = await screen.findByRole("table", { name: "Repositories" });
    expect(within(table).getByText("UNKNOWN")).toBeInTheDocument();
    expect(within(table).getByText("PROTECTED")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: /payments-api/ })).toHaveAttribute("href", "/repositories/5001");
  });

  it("filters on the server", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories", page([]));
    renderApp("/repositories");
    await screen.findByText("No repositories yet.");
    await user.selectOptions(screen.getByLabelText("Protection"), "unprotected");
    await waitFor(() => expect(api.calls.some((c) => c.path === "/api/v1/repositories" && c.search.includes("protection=unprotected"))).toBe(true));
    expect(await screen.findByText("No repositories match these filters.")).toBeInTheDocument();
  });

  it("shows enforcement signals without claiming protection", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/5001", data(f.repositoryDetail()));
    renderApp("/repositories/5001");
    expect(await screen.findByRole("heading", { name: "octo-org/payments-api" })).toBeInTheDocument();
    const enforcement = screen.getByRole("region", { name: "Enforcement" });
    expect(within(enforcement).getByText("NOT DETECTED")).toBeInTheDocument();
    expect(within(enforcement).getByText("NOT VERIFIABLE")).toBeInTheDocument();
    expect(within(enforcement).getAllByText("UNKNOWN").length).toBeGreaterThan(0);
    expect(screen.queryByText("PROTECTED")).toBeNull();
  });

  it("requires a reason and acknowledgement to pause monitoring", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/5001", data(f.repositoryDetail()));
    api.on("PUT", "/api/v1/repositories/5001/monitoring", data(f.repositoryDetail({ repository: f.repository({ monitoring_enabled: false, protection: "unprotected" }) })));
    renderApp("/repositories/5001");
    await user.click(await screen.findByRole("button", { name: /Pause monitoring/ }));
    const dialog = screen.getByRole("dialog", { name: "Pause CommitGuard monitoring?" });
    const confirm = within(dialog).getByRole("button", { name: "Pause monitoring" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "migrating to a new org");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(confirm);
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ enabled: false, reason: "migrating to a new org", confirm: true }));
  });

  it("uses the 404 view for missing or foreign repositories", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/7001", apiError(404, "NOT_FOUND"));
    renderApp("/repositories/7001");
    expect(await screen.findByRole("heading", { name: "This repository was not found" })).toBeInTheDocument();
  });
});

describe("scans", () => {
  it("renders the server's result and never re-derives it", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/scans", page([f.scan({ result: "blocked", findings: 0, violations: 0 }), f.scan({ id: "c".repeat(32), result: "warning" })]));
    renderApp("/scans");
    const table = await screen.findByRole("table", { name: "Scans" });
    expect(within(table).getByText("BLOCKED")).toBeInTheDocument();
    expect(within(table).getByText("WARNING")).toBeInTheDocument();
  });

  it("paginates with cursors", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/scans", ({ url }) => (url.searchParams.get("cursor") ? page([f.scan({ id: "d".repeat(32), repository: { id: 2, installation_id: 42, full_name: "octo-org/page-two" } })]) : page([f.scan()], "next-cursor")));
    renderApp("/scans");
    await screen.findByText("octo-org/payments-api");
    await user.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByText("octo-org/page-two")).toBeInTheDocument();
    expect(api.calls.some((c) => c.search.includes("cursor=next-cursor"))).toBe(true);
    expect(screen.getByText("Page 2")).toBeInTheDocument();
  });

  it("polls a running scan until it completes, then announces the result", async () => {
    const api = mockFetch();
    let calls = 0;
    api.on("GET", `/api/v1/scans/${"a".repeat(32)}`, () => {
      calls += 1;
      return data(calls === 1 ? f.scanDetail({ findings: [], conclusion: null }, { result: "running", completed_at: null }) : f.scanDetail());
    });
    api.on("GET", `/api/v1/scans/${"a".repeat(32)}/comparison`, data({ scan_id: "a".repeat(32), previous_scan_id: null, new: [], resolved: [], unchanged: [], new_findings: [], resolved_findings: [] }));
    renderApp(`/scans/${"a".repeat(32)}`);
    expect(await screen.findByText(/Scan running. This page updates automatically./)).toBeInTheDocument();
    expect(await screen.findByText("Scan completed: BLOCKED", {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getAllByText("BLOCKED").length).toBeGreaterThan(0);
  });

  it("shows findings, evidence and reproducibility metadata", async () => {
    const api = mockFetch();
    api.on("GET", `/api/v1/scans/${"a".repeat(32)}`, data(f.scanDetail()));
    api.on("GET", `/api/v1/scans/${"a".repeat(32)}/comparison`, data({ scan_id: "a".repeat(32), previous_scan_id: "b".repeat(32), new: ["x"], resolved: [], unchanged: [], new_findings: [], resolved_findings: [] }));
    renderApp(`/scans/${"a".repeat(32)}`);
    expect(await screen.findByText("Organization policy v2")).toBeInTheDocument();
    expect(screen.getByText("0.1.0.dev0")).toBeInTheDocument();
    expect(screen.getByText("Claude <noreply@anthropic.com>")).toBeInTheDocument();
    expect(await screen.findByText(/previous completed scan/)).toBeInTheDocument();
  });

  it("explains a failed scan as a failure, not a pass", async () => {
    const api = mockFetch();
    api.on("GET", `/api/v1/scans/${"a".repeat(32)}`, data(f.scanDetail({ failure: { kind: "infrastructure", message: "GitHub API unavailable" }, findings: [], can_rescan: false, rescan_blocked_reason: "A newer scan exists." }, { result: "error" })));
    renderApp(`/scans/${"a".repeat(32)}`);
    expect(await screen.findByText(/could not be completed: GitHub API unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/fails closed/)).toBeInTheDocument();
    expect(screen.queryByText("PASS")).toBeNull();
    expect(screen.queryByRole("button", { name: /Scan again/ })).toBeNull();
  });
});

describe("violations", () => {
  it("lists and filters violations", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/violations", page([f.violation(), f.violation({ id: "c".repeat(32), status: "resolved", severity: "low", rule_id: "bot_identity", title: "Bot identity detected" })]));
    renderApp("/violations?status=open");
    const table = await screen.findByRole("table", { name: "Violations" });
    expect(within(table).getByText("RESOLVED")).toBeInTheDocument();
    expect(api.calls.find((c) => c.path === "/api/v1/violations")?.search).toContain("status=open");
  });

  it("shows the empty state", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/violations", page([]));
    renderApp("/violations");
    expect(await screen.findByText("No violations detected.")).toBeInTheDocument();
  });

  it("shows evidence, remediation and lifecycle; XSS in metadata stays text", async () => {
    const api = mockFetch();
    const xss = "<script>window.__xss=1</script>";
    api.on("GET", `/api/v1/violations/${"b".repeat(32)}`, data(f.violationDetail({ committer: xss }, { author: `Mallory ${xss}` })));
    const { container } = renderApp(`/violations/${"b".repeat(32)}`);
    expect(await screen.findByRole("heading", { name: "Recommended action" })).toBeInTheDocument();
    expect(screen.getByText(/Remove the Co-authored-by line/)).toBeInTheDocument();
    expect(screen.getByText("CommitGuard does not rewrite Git history or modify commits automatically.")).toBeInTheDocument();
    expect(screen.getByText(xss)).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __xss?: number }).__xss).toBeUndefined();
    expect(screen.getByText(/Detected in pull request #7/)).toBeInTheDocument();
  });

  it("acknowledges without offering to resolve", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", `/api/v1/violations/${"b".repeat(32)}`, data(f.violationDetail()));
    api.on("PUT", `/api/v1/violations/${"b".repeat(32)}/acknowledgement`, data(f.violationDetail({ acknowledgement: { by: "alice", at: "2026-09-01T12:00:00Z", note: "reviewed" } }, { status: "acknowledged" })));
    renderApp(`/violations/${"b".repeat(32)}`);
    await user.type(await screen.findByLabelText("Note (optional)"), "reviewed");
    await user.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText(/Acknowledged by/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Resolve/ })).toBeNull();
    expect(api.calls.find((c) => c.method === "PUT")?.headers.get("X-CSRF-Token")).toBe("c".repeat(64));
  });

  it("explains a resolved violation and keeps its history", async () => {
    const api = mockFetch();
    api.on("GET", `/api/v1/violations/${"b".repeat(32)}`, data(f.violationDetail({ resolution: "no longer part of pull request #7", resolved_at: "2026-09-01T13:00:00Z", can_manage: false, exposures: [{ kind: "pull_request", label: "#7", active: false, opened_at: "2026-09-01T12:00:00Z", closed_at: "2026-09-01T13:00:00Z", closed_reason: "no longer part of pull request #7" }] }, { status: "resolved" })));
    renderApp(`/violations/${"b".repeat(32)}`);
    expect(await screen.findByText("No longer present")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Scans that detected/ })).toHaveTextContent("BLOCKED");
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
  });
});

describe("policies", () => {
  it("is read-only for viewers", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", "/api/v1/policies", data([f.policy(false)]));
    renderApp("/policies");
    expect(await screen.findByText(/Your role can view this policy/)).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /Organization floor/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save policy" })).toBeNull();
  });

  it("confirms a weakening change with current and new values before saving", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/policies", data([f.policy(true, { ai_coauthor: "block" })]));
    api.on("POST", "/api/v1/policies/1001/preview", data({ version: 3, weakening: true, changes: [{ policy_id: "ai_coauthor", old: "block", new: null, weakening: true }] }));
    api.on("PUT", "/api/v1/policies/1001", data(f.policy(true, {}), { changes: [] }));
    renderApp("/policies");
    await user.selectOptions(await screen.findByLabelText("Organization floor for ai_coauthor"), "");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    const dialog = await screen.findByRole("dialog", { name: "You are weakening a security enforcement rule" });
    expect(within(dialog).getByText("Always BLOCK")).toBeInTheDocument();
    expect(within(dialog).getByText("Repository decides")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Weaken enforcement" });
    expect(confirm).toBeDisabled();
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
    await user.type(within(dialog).getByLabelText(/Reason/), "moving to repository policy");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(confirm);
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toEqual({ expected_version: 3, floors: { ai_coauthor: null, bot_identity: null }, reason: "moving to repository policy", confirm_weakening: true }));
  });

  it("surfaces concurrent edits instead of overwriting", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/policies", data([f.policy(true, {})]));
    api.on("POST", "/api/v1/policies/1001/preview", data({ version: 3, weakening: false, changes: [] }));
    api.on("PUT", "/api/v1/policies/1001", apiError(409, "CONFLICT", "The policy was changed by someone else (now version 4)."));
    renderApp("/policies");
    await user.selectOptions(await screen.findByLabelText("Organization floor for bot_identity"), "block");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Someone else changed this policy")).toBeInTheDocument();
  });

  it("asks for a fresh sign-in when the server requires it", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    api.on("GET", "/api/v1/policies", data([f.policy(true, {})]));
    api.on("POST", "/api/v1/policies/1001/preview", data({ version: 3, weakening: false, changes: [] }));
    api.on("PUT", "/api/v1/policies/1001", apiError(401, "REAUTHENTICATION_REQUIRED", "Sign in again."));
    renderApp("/policies");
    await user.selectOptions(await screen.findByLabelText("Organization floor for bot_identity"), "warn");
    await user.click(screen.getByRole("button", { name: "Save policy" }));
    expect(await screen.findByText("Confirm your identity")).toBeInTheDocument();
    expect(location()).toBe("/policies");
  });
});

describe("rules, audit, GitHub and settings", () => {
  it("lists bundled rules and shows rule detail", async () => {
    const api = mockFetch();
    const rule = { id: "ai_coauthor", name: "AI co-author attribution", description: "desc", detector: "coauthor", severity: "high", default_action: "block", source: "bundled", trusted: true, status: "active", rules_version: "57b1df750182", tool_version: "0.1.0.dev0" };
    api.on("GET", "/api/v1/rules", data([rule]));
    api.on("GET", "/api/v1/rules/ai_coauthor", data({ rule, remediation: ["Remove it."], evidence_sources: ["Co-authored-by trailer"], data_files: [{ name: "patterns.yaml", entries: 12 }], editable: false }));
    renderApp("/rules");
    expect(await screen.findByText("Bundled · trusted")).toBeInTheDocument();
    renderApp("/rules/ai_coauthor");
    expect(await screen.findByText(/cannot be edited here/)).toBeInTheDocument();
  });

  it("shows the audit log read-only and denies roles without access", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/audit", page([f.auditEvent()]));
    renderApp("/audit");
    expect(await screen.findByText(/Changed organization policy v2 → v3/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete|Edit/ })).toBeNull();
    const denied = mockFetch();
    denied.on("GET", "/api/v1/audit", apiError(403, "FORBIDDEN", "You do not have permission to access this resource."));
    renderApp("/audit");
    expect(await screen.findByText("Access denied")).toBeInTheDocument();
  });

  it("shows installations and syncs repositories", async () => {
    const user = userEvent.setup();
    const api = mockFetch();
    const installation = { id: 42, account: { id: 1001, login: "octo-org", type: "Organization" }, status: "connected", repository_selection: "selected", repositories: 3, permissions: { checks: "write", contents: "read", metadata: "read", pull_requests: "read" }, missing_permissions: [], excessive_permissions: [], installed_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z", last_event_at: null, github_settings_url: "https://github.com/organizations/octo-org/settings/installations/42", can_manage: true };
    api.on("GET", "/api/v1/github/installations/42", data({ installation, recent_events: [] }));
    api.on("GET", "/api/v1/github/installations/42/repositories", page([{ id: 5001, full_name: "octo-org/payments-api", connected: true, monitoring_enabled: true, added_at: null, removed_at: null }]));
    api.on("POST", "/api/v1/github/installations/42/sync", data({ installation_id: 42, repositories: 3, added: ["octo-org/new"], removed: [], synced_at: "2026-09-01T12:00:00Z" }));
    renderApp("/github/installations/42");
    expect(await screen.findByRole("link", { name: /Manage on GitHub/ })).toHaveAttribute("href", installation.github_settings_url);
    await user.click(screen.getByRole("button", { name: /Sync repositories/ }));
    expect(await screen.findByText("Repositories synchronized")).toBeInTheDocument();
  });

  it("lists sessions and revokes another one", async () => {
    const user = userEvent.setup();
    const api = mockFetch(f.session("owner"));
    const current = f.session().session;
    api.on("GET", "/api/v1/auth/sessions", data([current, { ...current, id: "fedcba9876543210", current: false, user_agent: "Chrome on macOS" }]));
    api.on("DELETE", "/api/v1/auth/sessions/fedcba9876543210", data({ revoked: true }));
    api.on("GET", "/api/v1/organizations/1001/members", page([{ user_id: 501, login: "alice", role: "owner", granted_by: "cli", created_at: "2026-09-01T12:00:00Z", updated_at: "2026-09-01T12:00:00Z", implicit: false }]));
    renderApp("/settings");
    const item = (await screen.findByText("Chrome on macOS")).closest("li")!;
    await user.click(within(item).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "DELETE")).toBe(true));
    expect(screen.getByText("This browser")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Grant role" })).toBeInTheDocument();
  });
});
