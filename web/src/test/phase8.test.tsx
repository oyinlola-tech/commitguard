import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { idempotencyKey } from "../api/bulk";
import { getEffectivePolicy, listRepositoryMatrix, reportUrl } from "../api/governance";
import { archiveGroup } from "../api/groups";
import { ApiError, setCsrfToken } from "../api/client";
import type { BulkOperation, Rule } from "../api/types";
import { relaxedControls } from "../pages/OrganizationSettings";
import * as f from "./fixtures";
import { apiError, data, mockFetch, page, renderApp } from "./render";

const ORG = "/api/v1/organizations/1001";

function rule(id: string, severity: Rule["severity"], name: string): Rule {
  return { id, name, description: `${name} description`, detector: "coauthor", severity, default_action: "block", source: "bundled", trusted: true, status: "active", rules_version: "57b1", tool_version: "0.1.0" };
}

function bulk(overrides: Partial<BulkOperation> = {}): BulkOperation {
  return {
    id: "6".repeat(32),
    organization_id: 1001,
    type: "add_to_group",
    parameters: { group_id: f.GROUP_ID },
    status: "queued",
    total: 1,
    completed: 0,
    failed: 0,
    skipped: 0,
    pending: 1,
    requested_by: "alice",
    created_at: "2026-09-01T12:00:00Z",
    started_at: null,
    completed_at: null,
    cancelled_by: null,
    items: [{ repository_id: 5001, full_name: "octo-org/payments-api", status: "pending", attempts: 0, detail: null }],
    can_manage: true,
    ...overrides,
  };
}

describe("governance API client", () => {
  it("passes matrix filters to the server and reads total and computed_at", async () => {
    const api = mockFetch();
    api.on("GET", `${ORG}/security/repositories`, { body: { data: [f.repositoryPosture()], meta: { next_cursor: "WzI1XQ", limit: 25, total: 40, computed_at: "2026-09-01T12:00:00Z" } } });
    const result = await listRepositoryMatrix(1001, { group: f.GROUP_ID, posture: "at_risk", q: "" });
    expect(result.total).toBe(40);
    expect(result.nextCursor).toBe("WzI1XQ");
    expect(result.computedAt).toBe("2026-09-01T12:00:00Z");
    const call = api.calls.find((c) => c.path === `${ORG}/security/repositories`);
    expect(call?.search).toBe(`?group=${f.GROUP_ID}&posture=at_risk`);
  });

  it("archives a group with DELETE, confirmation in the query and the CSRF token", async () => {
    const api = mockFetch();
    setCsrfToken("t".repeat(64));
    api.on("DELETE", `/api/v1/repository-groups/${f.GROUP_ID}`, data({ archived: true }));
    expect(await archiveGroup(f.GROUP_ID, true)).toEqual({ archived: true });
    const call = api.calls.find((c) => c.method === "DELETE");
    expect(call?.search).toBe("?confirm=true");
    expect(call?.headers.get("X-CSRF-Token")).toBe("t".repeat(64));
    setCsrfToken(null);
  });

  it("returns effective policy exception counts from the envelope meta", async () => {
    const api = mockFetch();
    api.on("GET", "/api/v1/repositories/5001/effective-policy", data(f.effectivePolicy(), { exceptions: { active: 2, expiring_soon: 1 } }));
    const result = await getEffectivePolicy(5001);
    expect(result.exceptions).toEqual({ active: 2, expiring_soon: 1 });
    expect(result.view.propagation).toBe("up_to_date");
  });

  it("builds same-origin report links and random idempotency keys", () => {
    expect(reportUrl(1001, "compliance", "csv")).toBe("/api/v1/organizations/1001/reports/compliance?format=csv");
    const key = idempotencyKey();
    expect(key).toMatch(/^[0-9a-f]{32}$/);
    expect(idempotencyKey()).not.toBe(key);
  });

  it("reads relaxed controls from the error details, falling back to the message", () => {
    const structured = new ApiError(409, "CONFIRMATION_REQUIRED", "This change relaxes security controls and must be confirmed: a; b", null, null, {
      changes: ["permanent exceptions allowed", "policy approval no longer required"],
    });
    expect(relaxedControls(structured)).toEqual(["permanent exceptions allowed", "policy approval no longer required"]);
    expect(relaxedControls(new ApiError(409, "CONFIRMATION_REQUIRED", "Confirm this"))).toEqual(["Confirm this"]);
  });
});

describe("organization command center", () => {
  it("shows the posture with its reasons and the server's compliance sentence, never a score", async () => {
    const api = mockFetch(f.session("owner"));
    api.on("GET", `${ORG}/security/overview`, data(f.organizationPosture()));
    api.on("GET", `${ORG}/security/events`, data([f.securityEvent({ type: "policy_changed", severity: "critical", title: "Organization policy changed", body: "alice weakened ai_coauthor.", resource_type: "policy", resource_id: "1001" })]));
    api.on("POST", `${ORG}/security/events/${"7".repeat(32)}/acknowledge`, data({ event_id: "7".repeat(32), acknowledged_by: "alice", acknowledged_at: "2026-09-01T12:00:00Z", meaning: "seen" }));
    renderApp("/organization");
    const posture = await screen.findByRole("region", { name: "Organization posture" });
    expect(within(posture).getByText("AT RISK")).toBeInTheDocument();
    expect(within(posture).getByText("1 repository(ies) at risk.")).toBeInTheDocument();
    expect(within(posture).getByText("Installation octo-org (42): The last synchronisation did not finish.")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 required repositories satisfy all mandatory controls")).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByText("The last synchronisation did not finish.")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Acknowledge: Organization policy changed/ }));
    const dialog = screen.getByRole("dialog", { name: "Acknowledge this event" });
    expect(within(dialog).getByText(/does not resolve a violation/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path.endsWith("/acknowledge"))).toBe(true));
  });

  it("does not offer acknowledgement to a viewer", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", `${ORG}/security/overview`, data(f.organizationPosture()));
    api.on("GET", `${ORG}/security/events`, data([f.securityEvent({ title: "Changed", body: "b" })]));
    renderApp("/organization");
    expect(await screen.findByText("Changed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Acknowledge/ })).toBeNull();
  });
});

describe("repository security matrix", () => {
  function setup(role: "viewer" | "admin" = "admin") {
    const api = mockFetch(f.session(role));
    api.on("GET", `${ORG}/security/repositories`, page([
      f.repositoryPosture(),
      f.repositoryPosture({
        repository_id: 5002,
        full_name: "octo-org/web-console",
        posture: "at_risk",
        posture_reasons: ["Monitor mode: violations are reported but not blocked."],
        mode: "monitor",
        drift: "drift",
        drift_differences: [{ policy_id: "ai_coauthor", requested: "warn", requested_by: "Repository configuration (.commitguard.yaml)", required: "block", required_by: "organization policy v3", effective: "block" }],
      }),
    ]));
    api.on("GET", `${ORG}/repository-groups`, data([f.group()]));
    api.on("GET", `${ORG}/bulk-operations`, data([]));
    return api;
  }

  it("keeps filters in the URL, sends them to the server and explains drift", async () => {
    const api = setup();
    renderApp("/organization/repositories");
    const table = await screen.findByRole("table", { name: "Repository security matrix" });
    expect(within(table).getByText("octo-org/web-console")).toBeInTheDocument();
    expect(within(table).getByText("Repository configuration (.commitguard.yaml):", { exact: false })).toBeInTheDocument();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Posture"), "at_risk");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/organization/repositories?posture=at_risk"));
    await waitFor(() => expect(api.calls.some((c) => c.path === `${ORG}/security/repositories` && c.search.includes("posture=at_risk"))).toBe(true));
    await user.selectOptions(screen.getByLabelText("Group"), f.GROUP_ID);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`group=${f.GROUP_ID}`));
  });

  it("enables the bulk bar on selection and queues an idempotent bulk operation", async () => {
    const api = setup();
    api.on("POST", `${ORG}/bulk-operations`, { status: 202, body: { data: bulk(), meta: {} } });
    api.on("GET", `/api/v1/bulk-operations/${"6".repeat(32)}`, data(bulk({ status: "completed", completed: 1, pending: 0, items: [] })));
    renderApp("/organization/repositories");
    const user = userEvent.setup();
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    await user.click(await screen.findByRole("checkbox", { name: "Select octo-org/payments-api" }));
    const bar = screen.getByRole("region", { name: "Bulk actions" });
    expect(within(bar).getByText("1 repository selected")).toBeInTheDocument();
    await user.click(within(bar).getByRole("button", { name: /Add to group/ }));
    const dialog = await screen.findByRole("dialog", { name: "Add repositories to a group" });
    const confirm = within(dialog).getByRole("button", { name: /Add repositories to a group \(1 repository\)/ });
    expect(confirm).toBeDisabled();
    await user.selectOptions(await within(dialog).findByLabelText("Repository group"), f.GROUP_ID);
    await user.click(confirm);
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST" && c.path === `${ORG}/bulk-operations`)).toBe(true));
    const call = api.calls.find((c) => c.method === "POST" && c.path === `${ORG}/bulk-operations`);
    expect(call?.body).toMatchObject({ type: "add_to_group", repository_ids: [5001], parameters: { group_id: f.GROUP_ID }, confirm: false });
    expect((call?.body as { idempotency_key: string }).idempotency_key).toMatch(/^[0-9a-f]{32}$/);
    const progress = await screen.findByRole("region", { name: "Bulk operation" });
    expect(await within(progress).findByText(/1 completed · 0 failed · 0 skipped · 0 pending/)).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`operation=${"6".repeat(32)}`);
  });

  it("requires the enforce warning and confirmation before changing modes", async () => {
    const api = setup();
    api.on("POST", `${ORG}/bulk-operations`, { status: 202, body: { data: bulk({ type: "set_mode", parameters: { mode: "monitor" } }), meta: {} } });
    renderApp("/organization/repositories");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("checkbox", { name: "Select every repository on this page" }));
    await user.click(screen.getByRole("button", { name: "Change mode" }));
    const dialog = await screen.findByRole("dialog", { name: "Change enforcement mode" });
    expect(within(dialog).getByText("This change may cause GitHub checks to fail and prevent merges.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: /Monitor/ }));
    await user.click(within(dialog).getByLabelText(/I understand/));
    const confirm = within(dialog).getByRole("button", { name: /Change enforcement mode \(2 repositories\)/ });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "noisy during migration");
    await user.click(confirm);
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    expect(api.calls.find((c) => c.method === "POST")?.body).toMatchObject({ type: "set_mode", confirm: true, parameters: { mode: "monitor", reason: "noisy during migration" } });
  });

  it("shows a viewer the matrix without selection or write actions", async () => {
    setup("viewer");
    renderApp("/organization/repositories");
    expect(await screen.findByRole("table", { name: "Repository security matrix" })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Select/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Add repositories/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Create group/ })).toBeNull();
  });
});

describe("policy draft review", () => {
  it("explains separation of duties to the author and labels the simulation", async () => {
    const api = mockFetch(f.session("admin"));
    api.on("GET", `/api/v1/policy-drafts/${f.DRAFT_ID}`, data(f.draft()));
    api.on("GET", `${ORG}/simulations`, data([f.simulation()]));
    api.on("GET", `/api/v1/simulations/${"5".repeat(32)}`, data(f.simulation()));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    expect(await screen.findByText("Separation of duties: the author of a policy change cannot approve it.", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
    const simulation = await screen.findByRole("region", { name: "SIMULATION" });
    expect(within(simulation).getByText(/an estimate from recorded scans/)).toBeInTheDocument();
    expect(within(simulation).getAllByText("New blocks").length).toBeGreaterThan(0);
    expect(within(simulation).getByRole("link", { name: "octo-org/payments-api" })).toBeInTheDocument();
  });

  it("requires explicit confirmation to publish a weakening change", async () => {
    const api = mockFetch(f.session("admin"));
    const weakening = f.draft({
      state: "approved",
      created_by: "ada",
      submitted_by: "ada",
      weakening: true,
      can_publish: true,
      can_edit: false,
      changes: [{ policy_id: "ai_coauthor", old: "block", new: "warn", weakening: true, enforcement: "mandatory" }],
    });
    api.on("GET", `/api/v1/policy-drafts/${f.DRAFT_ID}`, data(weakening));
    api.on("GET", `${ORG}/simulations`, data([]));
    api.on("GET", `${ORG}/settings`, data(f.settingsView()));
    api.on("GET", `${ORG}/security/repositories`, page([f.repositoryPosture()]));
    api.on("POST", `/api/v1/policy-drafts/${f.DRAFT_ID}/publish`, data({ ...weakening, state: "published", published_version: 4, can_publish: false }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const user = userEvent.setup();
    expect(screen.queryByText(/Separation of duties/)).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish a change that weakens enforcement" });
    expect(within(dialog).getByText("Weakens enforcement")).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Publish v4" });
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByLabelText(/I confirm publishing a change that weakens enforcement/));
    await user.click(confirm);
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith("/publish"))).toBe(true));
    expect(api.calls.find((c) => c.path.endsWith("/publish"))?.body).toEqual({ confirm_weakening: true });
    expect(await screen.findByText("Published as v4")).toBeInTheDocument();
  });

  it("asks for a new sign-in when the server requires it", async () => {
    const api = mockFetch(f.session("admin"));
    api.on("GET", `/api/v1/policy-drafts/${f.DRAFT_ID}`, data(f.draft({ state: "approved", created_by: "ada", submitted_by: "ada", can_publish: true })));
    api.on("GET", `${ORG}/simulations`, data([]));
    api.on("GET", `${ORG}/settings`, data(f.settingsView()));
    api.on("POST", `/api/v1/policy-drafts/${f.DRAFT_ID}/publish`, apiError(401, "REAUTHENTICATION_REQUIRED", "Sign in again."));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Publish v4" }));
    expect(await within(dialog).findByText("Confirm your identity")).toBeInTheDocument();
  });
});

describe("exception requests", () => {
  function setup() {
    const api = mockFetch(f.session("admin"));
    api.on("GET", "/api/v1/rules", data([rule("ai_coauthor", "high", "AI co-author attribution"), rule("malformed_trailer", "low", "Malformed trailer")]));
    api.on("GET", `${ORG}/settings`, data(f.settingsView()));
    api.on("GET", `${ORG}/security/repositories`, page([f.repositoryPosture()]));
    api.on("GET", `${ORG}/repository-groups`, data([f.group()]));
    return api;
  }

  it("says when approval will be needed and why", async () => {
    setup();
    renderApp("/organization/exceptions/new");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Rule"), "ai_coauthor");
    const approval = screen.getByRole("region", { name: "Approval" });
    expect(within(approval).getByText("Approval will be needed")).toBeInTheDocument();
    expect(within(approval).getByText(/the rule's severity \(HIGH\) is at or above/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Rule"), "malformed_trailer");
    expect(within(approval).getByText("No approval expected")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Group" }));
    expect(within(approval).getByText(/it covers a whole group/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Permanent exception/)).toBeNull();
  });

  it("requests an expiring exception for a repository", async () => {
    const api = setup();
    api.on("POST", `${ORG}/exceptions`, { status: 201, body: { data: f.policyException({ id: "8".repeat(32) }), meta: {} } });
    api.on("GET", `/api/v1/exceptions/${"8".repeat(32)}`, data(f.policyException({ id: "8".repeat(32) })));
    renderApp("/organization/exceptions/new");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Rule"), "malformed_trailer");
    await user.selectOptions(await screen.findByRole("combobox", { name: "Repository" }), "5001");
    await user.click(screen.getByRole("radio", { name: /ALLOW/ }));
    await user.type(screen.getByLabelText(/Justification/), "Generated trailers");
    await user.click(screen.getByRole("button", { name: /Request exception/ }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "POST")).toBe(true));
    const body = api.calls.find((c) => c.method === "POST")?.body as Record<string, unknown>;
    expect(body).toMatchObject({ rule_id: "malformed_trailer", scope_type: "repository", scope_id: 5001, action: "allow", permanent: false, reason: "Generated trailers" });
    expect(typeof body.expires_at).toBe("string");
    expect(await screen.findByRole("heading", { name: "Malformed trailer exception" })).toBeInTheDocument();
    expect(screen.getByText("Expiring soon")).toBeInTheDocument();
  });
});

describe("organization settings", () => {
  it("lists the relaxed controls and requires a reason before confirming", async () => {
    const api = mockFetch(f.session("owner"));
    let puts = 0;
    api.on("GET", `${ORG}/settings`, () => data(f.settingsView({ version: puts >= 2 ? 3 : 2 })));
    api.on("GET", "/api/v1/rules", data([rule("ai_coauthor", "high", "AI co-author attribution")]));
    api.on("GET", `${ORG}/rules`, data({ organization_id: 1001, version: 0, fingerprint: null, document: { ai_identities: [], bot_identities: [] }, rules_version: "57b1", created_at: null, created_by: null, reason: null, trust_levels: [], can_manage: true }));
    api.on("GET", `${ORG}/rules/history`, data([]));
    api.on("GET", `${ORG}/scan-schedules`, data([]));
    api.on("PUT", `${ORG}/settings`, ({ body }) => {
      puts += 1;
      if (!(body as { confirm: boolean }).confirm) {
        return {
          status: 409,
          body: {
            error: {
              code: "CONFIRMATION_REQUIRED",
              message: "This change relaxes security controls and must be confirmed: longer exceptions allowed (90 -> 120 days)",
              request_id: "r",
              details: { changes: ["longer exceptions allowed (90 -> 120 days)"] },
            },
          },
        };
      }
      return data(f.settingsView({ version: 3 }));
    });
    renderApp("/settings/organization");
    const user = userEvent.setup();
    const days = await screen.findByLabelText(/Longest exception/);
    await user.clear(days);
    await user.type(days, "120");
    await user.click(screen.getByRole("button", { name: /Save settings/ }));
    const dialog = await screen.findByRole("dialog", { name: "You are relaxing security controls" });
    expect(within(dialog).getByText("longer exceptions allowed (90 -> 120 days)")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(1);
    const confirm = within(dialog).getByRole("button", { name: "Relax controls" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason \(required/), "long migrations");
    await user.click(within(dialog).getByLabelText(/I understand/));
    await user.click(confirm);
    expect(await screen.findByText(/Saved as version 3/)).toBeInTheDocument();
    expect(puts).toBe(2);
    const last = api.calls.filter((c) => c.method === "PUT").at(-1);
    expect(last?.body).toEqual({ expected_version: 2, settings: { exception_max_days: 120 }, reason: "long migrations", confirm: true });
  });
});

describe("effective policy on the repository page", () => {
  it("renders conflicts with the server's reason and the propagation state", async () => {
    const api = mockFetch(f.session("admin"));
    api.on("GET", "/api/v1/repositories/5001", data(f.repositoryDetail()));
    api.on("GET", `${ORG}/repository-groups`, data([f.group()]));
    const conflict = {
      policy_id: "ai_coauthor",
      requested_action: "allow" as const,
      requested_enabled: true,
      requested_by: "repository_configuration" as const,
      requested_label: "Repository configuration (.commitguard.yaml)",
      required_action: "block" as const,
      required_by: "organization" as const,
      required_label: "organization policy v3",
      effective_action: "block" as const,
      reason: "Organization policy is mandatory.",
    };
    const scanned = f.effectivePolicy().effective;
    api.on(
      "GET",
      "/api/v1/repositories/5001/effective-policy",
      data(
        f.effectivePolicy({
          propagation: "stale",
          last_scan_used_current_policy: false,
          last_scan_effective: { ...scanned, rules: [f.provenance({ source: "repository_configuration", conflict })] },
        }),
        { exceptions: { active: 1, expiring_soon: 1 } },
      ),
    );
    renderApp("/repositories/5001");
    const panel = await screen.findByRole("region", { name: "Effective policy" });
    const found = await within(panel).findByRole("region", { name: "Policy conflict for ai_coauthor" });
    expect(within(found).getByText("octo-org/payments-api")).toBeInTheDocument();
    expect(within(found).getByText("ALLOW")).toBeInTheDocument();
    expect(within(found).getByText("Organization requirement")).toBeInTheDocument();
    expect(within(found).getAllByText("BLOCK")).toHaveLength(2);
    expect(within(found).getByText("Organization policy is mandatory.")).toBeInTheDocument();
    expect(within(panel).getByText("STALE")).toBeInTheDocument();
    expect(within(panel).getByText("used an earlier policy")).toBeInTheDocument();
    expect(within(panel).getByText(/only known at scan time/)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: /1 active · 1 expiring/ })).toBeInTheDocument();
    expect(await within(panel).findByRole("link", { name: /Production/ })).toHaveAttribute("href", `/organization/groups/${f.GROUP_ID}`);
  });
});

describe("organization navigation", () => {
  it("shows organization pages the role can use and hides the others", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", `${ORG}/security/overview`, data(f.organizationPosture()));
    api.on("GET", `${ORG}/security/events`, data([]));
    renderApp("/organization");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Command center" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Exceptions" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Organization audit" })).toBeNull();
  });
});

describe("security event links", () => {
  it("links events to the pages of their resources, and nothing else", async () => {
    const api = mockFetch(f.session("viewer"));
    api.on("GET", `${ORG}/security/overview`, data(f.organizationPosture()));
    api.on("GET", `${ORG}/security/events`, data([
      f.securityEvent({ id: "a1".repeat(16), title: "Approval requested" }),
      f.securityEvent({ id: "a2".repeat(16), title: "Repository protection lost", resource_type: "repository", resource_id: "5001", repository_id: 5001 }),
      f.securityEvent({ id: "a3".repeat(16), title: "Hidden repository event", resource_type: "repository", resource_id: "5009", repository_id: null }),
      f.securityEvent({ id: "a4".repeat(16), title: "Installation suspended", resource_type: "installation", resource_id: "42" }),
      f.securityEvent({ id: "a5".repeat(16), title: "Exception requested", resource_type: "exception", resource_id: f.EXCEPTION_ID }),
      f.securityEvent({ id: "a6".repeat(16), title: "Rollout paused", resource_type: "rollout", resource_id: "9".repeat(32) }),
      f.securityEvent({ id: "a7".repeat(16), title: "Settings changed", resource_type: "organization", resource_id: "1001" }),
      f.securityEvent({ id: "a8".repeat(16), title: "Tampered draft", resource_type: "draft", resource_id: "../../admin" }),
    ]));
    renderApp("/organization");
    const events = await screen.findByRole("region", { name: "Critical and high security events" });
    expect(await within(events).findByRole("link", { name: "Approval requested" })).toHaveAttribute("href", `/organization/policies/drafts/${f.DRAFT_ID}`);
    expect(within(events).getByRole("link", { name: "Repository protection lost" })).toHaveAttribute("href", "/repositories/5001");
    expect(within(events).getByRole("link", { name: "Installation suspended" })).toHaveAttribute("href", "/github/installations/42");
    expect(within(events).getByRole("link", { name: "Exception requested" })).toHaveAttribute("href", `/organization/exceptions/${f.EXCEPTION_ID}`);
    expect(within(events).getByRole("link", { name: "Rollout paused" })).toHaveAttribute("href", `/organization/policies/rollouts/${"9".repeat(32)}`);
    for (const title of ["Hidden repository event", "Settings changed", "Tampered draft"]) {
      expect(within(events).getByText(title).tagName).not.toBe("A");
    }
  });
});

describe("draft rebase, publication reason and flags", () => {
  function setup(draft: ReturnType<typeof f.draft>) {
    const api = mockFetch(f.session("admin"));
    api.on("GET", `/api/v1/policy-drafts/${f.DRAFT_ID}`, data(draft));
    api.on("GET", `${ORG}/simulations`, data([]));
    api.on("GET", `${ORG}/settings`, data(f.settingsView()));
    api.on("GET", `${ORG}/policy-targets/organization/versions`, data({ policy: f.policy(true), versions: [] }, { next_cursor: null, limit: 10 }));
    api.on("GET", `${ORG}/security/repositories`, page([f.repositoryPosture()]));
    return api;
  }

  it("rebases an editable draft on the current version", async () => {
    const stale = f.draft({ state: "draft", base_version: 2, current_version: 3, rebase_required: true, requires_approval: false, can_edit: true, can_publish: true, revision: 4, approvals: [] });
    const api = setup(stale);
    api.on("PATCH", `/api/v1/policy-drafts/${f.DRAFT_ID}`, data({ ...stale, base_version: 3, rebase_required: false, revision: 5 }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const user = userEvent.setup();
    expect(await screen.findByText("The target changed since this draft was created")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /New draft with these rules/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Rebase on v3" }));
    const dialog = await screen.findByRole("dialog", { name: "Rebase on v3" });
    expect(within(dialog).getByText(/cancels its approvals/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Rebase on v3" }));
    await waitFor(() => expect(api.calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ expected_revision: 4, rebase: true });
    await waitFor(() => expect(screen.queryByText("The target changed since this draft was created")).toBeNull());
  });

  it("offers a new draft instead of a rebase while the draft waits for approval", async () => {
    setup(f.draft({ created_by: "ada", submitted_by: "ada", base_version: 2, current_version: 3, rebase_required: true, can_edit: false }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    expect(await screen.findByRole("button", { name: /New draft with these rules/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rebase on/ })).toBeNull();
    expect(screen.getByText(/A draft waiting for approval cannot be rebased/)).toBeInTheDocument();
  });

  it("requires a publication reason for a weakening change without one", async () => {
    const weakening = f.draft({
      state: "approved",
      created_by: "ada",
      submitted_by: "ada",
      reason: null,
      weakening: true,
      can_publish: true,
      changes: [{ policy_id: "ai_coauthor", old: "block", new: "warn", weakening: true, enforcement: "mandatory" }],
    });
    const api = setup(weakening);
    api.on("POST", `/api/v1/policy-drafts/${f.DRAFT_ID}/publish`, data({ ...weakening, state: "published", published_version: 4 }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish a change that weakens enforcement" });
    const confirm = within(dialog).getByRole("button", { name: "Publish v4" });
    await user.click(within(dialog).getByLabelText(/I confirm publishing/));
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason \(required: this change weakens enforcement/), "temporary during migration");
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => expect(api.calls.some((c) => c.path.endsWith("/publish"))).toBe(true));
    expect(api.calls.find((c) => c.path.endsWith("/publish"))?.body).toEqual({ confirm_weakening: true, reason: "temporary during migration" });
  });

  it("keeps the reason optional when the change does not weaken enforcement", async () => {
    setup(f.draft({ state: "approved", created_by: "ada", submitted_by: "ada", can_publish: true }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish this policy change" });
    expect(within(dialog).getByLabelText(/Reason \(optional; replaces the draft's reason/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Publish v4" })).toBeEnabled();
  });

  it("follows the server's cancel and emergency flags", async () => {
    setup(f.draft({ created_by: "ada", submitted_by: "ada", can_cancel: false, can_emergency_publish: false }));
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    expect(await screen.findByText("Your role can review this change but not act on it.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Cancel draft/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Emergency publish/ })).toBeNull();
  });

  it("shows what a published draft changed and links its rollout", async () => {
    setup(
      f.draft({
        state: "published",
        published_version: 4,
        published_by: "ada",
        published_at: "2026-09-01T12:00:00Z",
        rollout_id: "9".repeat(32),
        can_cancel: false,
        can_emergency_publish: false,
        changes: [{ policy_id: "ai_trailer", old: null, new: "block", weakening: false, enforcement: "mandatory" }],
      }),
    );
    renderApp(`/organization/policies/drafts/${f.DRAFT_ID}`);
    const table = await screen.findByRole("table", { name: "Changes published in v4" });
    expect(within(table).getByRole("columnheader", { name: "v3" })).toBeInTheDocument();
    expect(within(table).getByText("ai_trailer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Follow the staged rollout" })).toHaveAttribute("href", `/organization/policies/rollouts/${"9".repeat(32)}`);
  });
});

describe("group exceptions", () => {
  it("asks the server for the group's exceptions", async () => {
    const api = mockFetch(f.session("admin"));
    api.on("GET", `/api/v1/repository-groups/${f.GROUP_ID}`, data({ group: f.group(), repositories: [], hidden_repositories: 0, can_manage: true }));
    api.on("GET", `${ORG}/policy-targets/group/versions`, data({ policy: { organization: { id: 1001, login: "octo-org", type: "Organization" }, target: { type: "group", id: f.GROUP_ID, label: "Group Production" }, version: 0, fingerprint: null, updated_at: null, updated_by: null, reason: null, rules: [], can_write: true }, versions: [] }));
    api.on("GET", `${ORG}/exceptions`, page([f.policyException({ scope: { type: "group", id: f.GROUP_ID, label: "Group Production" }, rule_name: "Automation or bot identity", rule_id: "bot_identity" })]));
    renderApp(`/organization/groups/${f.GROUP_ID}`);
    const panel = await screen.findByRole("region", { name: "Group exceptions" });
    expect(await within(panel).findByText("Automation or bot identity")).toBeInTheDocument();
    const call = api.calls.find((c) => c.path === `${ORG}/exceptions`);
    expect(call?.search).toContain(`group=${f.GROUP_ID}`);
    expect(api.calls.some((c) => c.path.endsWith("/security/exceptions"))).toBe(false);
  });
});
