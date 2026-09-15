import type {
  AuditEvent,
  Finding,
  OrganizationAccess,
  OrganizationPolicy,
  Overview,
  Permission,
  RepositoryDetail,
  RepositorySummary,
  ScanDetail,
  ScanSummary,
  SessionInfo,
  ViolationDetail,
  ViolationSummary,
} from "../api/types";

const NOW = "2026-09-01T12:00:00Z";
export const SHA = "8e71c2a4f09b1c2d3e4f5a6b7c8d9e0f1a2b3c4d";
export const BASE_SHA = "3a91f02e7d5c1b86a4e0f9d2c7b3a1e8d6f40c21";

const VIEWER: Permission[] = ["repositories:read", "scans:read", "violations:read", "policies:read", "rules:read"];
const ADMIN: Permission[] = [...VIEWER, "violations:manage", "scans:trigger", "audit:read", "policies:write", "repositories:manage", "github:manage", "members:read"];

export function organization(role: "viewer" | "admin" | "owner" = "admin"): OrganizationAccess {
  const permissions = role === "viewer" ? VIEWER : role === "owner" ? [...ADMIN, "members:manage" as Permission] : ADMIN;
  return { organization: { id: 1001, login: "octo-org", type: "Organization" }, role, implicit_role: false, permissions, installation_ids: [42] };
}

export function session(role: "viewer" | "admin" | "owner" = "admin", organizations?: OrganizationAccess[]): SessionInfo {
  return {
    user: { id: 501, login: "alice" },
    session: { id: "0123456789abcdef", created_at: NOW, last_seen_at: NOW, expires_at: "2026-09-01T20:00:00Z", user_agent: "Firefox on Linux", current: true },
    csrf_token: "c".repeat(64),
    organizations: organizations ?? [organization(role)],
    reauthentication_required_after: "2026-09-01T12:15:00Z",
  };
}

export function scan(overrides: Partial<ScanSummary> = {}): ScanSummary {
  return {
    id: "a".repeat(32),
    scan_id: "f".repeat(32),
    repository: { id: 5001, installation_id: 42, full_name: "octo-org/payments-api" },
    organization_id: 1001,
    event: "pull_request",
    pull_request_number: 7,
    ref: "refs/heads/main",
    base_sha: BASE_SHA,
    head_sha: SHA,
    check_name: "commitguard-app",
    result: "blocked",
    commits_scanned: 2,
    violations: 1,
    warnings: 0,
    findings: 1,
    created_at: NOW,
    started_at: NOW,
    completed_at: "2026-09-01T12:00:02Z",
    duration_ms: 2000,
    requested_by: null,
    ...overrides,
  };
}

export function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    id: 1,
    violation_id: "b".repeat(32),
    rule_id: "ai_coauthor",
    detector: "coauthor",
    title: "AI coauthor detected",
    message: "A co-author trailer names an identity associated with the AI agent Claude.",
    severity: "high",
    confidence: "high",
    action: "block",
    reason: "policy 'ai_coauthor' action is block",
    commit_sha: SHA,
    author: "Dana Reyes <dana@northwind.dev>",
    committer: "Dana Reyes <dana@northwind.dev>",
    evidence: [{ source: "coauthor_trailer", source_label: "Co-authored-by trailer", value: "Claude <noreply@anthropic.com>", line_number: 3, matched: [], notes: [] }],
    remediation: "Remove the AI co-author attribution from the commit message.",
    ...overrides,
  };
}

export function scanDetail(overrides: Partial<ScanDetail> = {}, summary: Partial<ScanSummary> = {}): ScanDetail {
  return {
    scan: scan(summary),
    conclusion: "failure",
    tool_version: "0.1.0.dev0",
    rules_version: "57b1df750182531f",
    policy_version: "ef334cbde9255add",
    policy_source: "pull request base (3a91f02e7d5c)",
    organization_policy_version: 2,
    effective_policies: [{ id: "ai_coauthor", enabled: true, action: "block" }],
    detector_failures: 0,
    notices: [],
    failure: null,
    findings: [finding()],
    can_rescan: true,
    rescan_blocked_reason: null,
    ...overrides,
  };
}

export function violation(overrides: Partial<ViolationSummary> = {}): ViolationSummary {
  return {
    id: "b".repeat(32),
    rule_id: "ai_coauthor",
    title: "AI coauthor detected",
    severity: "high",
    action: "block",
    repository: { id: 5001, installation_id: 42, full_name: "octo-org/payments-api" },
    organization_id: 1001,
    commit_sha: SHA,
    author: "Dana Reyes <dana@northwind.dev>",
    status: "open",
    first_detected_at: NOW,
    last_detected_at: NOW,
    detections: 1,
    ...overrides,
  };
}

export function violationDetail(overrides: Partial<ViolationDetail> = {}, summary: Partial<ViolationSummary> = {}): ViolationDetail {
  return {
    violation: violation(summary),
    detector: "coauthor",
    message: "A co-author trailer names an identity associated with the AI agent Claude.",
    committer: "Dana Reyes <dana@northwind.dev>",
    policy_reason: "policy 'ai_coauthor' action is block",
    evidence: finding().evidence,
    remediation: "Remove the AI co-author attribution.",
    recommended_steps: ["Remove the Co-authored-by line that names the AI agent from the commit message.", "CommitGuard does not rewrite Git history or modify commits automatically."],
    resolved_at: null,
    resolution: null,
    acknowledgement: null,
    exposures: [{ kind: "pull_request", label: "#7", active: true, opened_at: NOW, closed_at: null, closed_reason: null }],
    detections: [{ scan: "a".repeat(32), result: "blocked", detected_at: NOW, action: "block", head_sha: SHA }],
    first_scan_id: "a".repeat(32),
    last_scan_id: "a".repeat(32),
    can_manage: true,
    ...overrides,
  };
}

export function repository(overrides: Partial<RepositorySummary> = {}): RepositorySummary {
  return {
    id: 5001,
    installation_id: 42,
    organization: { id: 1001, login: "octo-org", type: "Organization" },
    owner: "octo-org",
    name: "payments-api",
    full_name: "octo-org/payments-api",
    github_url: "https://github.com/octo-org/payments-api",
    default_branch: "main",
    protection: "unknown",
    protection_reason: "Scanned by the GitHub App, but branch protection has not been verified.",
    app_connection: "connected",
    monitoring_enabled: true,
    last_scan: scan(),
    open_violations: 1,
    open_warnings: 0,
    critical_open: 0,
    ...overrides,
  };
}

export function repositoryDetail(overrides: Partial<RepositoryDetail> = {}): RepositoryDetail {
  return {
    repository: repository(),
    enforcement: {
      github_app: { status: "connected", detail: "The GitHub App can access this repository.", checked_at: null },
      github_actions: { status: "not_detected", detail: "no workflow on main runs CommitGuard", checked_at: NOW },
      required_check: { status: "unknown", branch: "main", required_checks: [], detail: "branch protection could not be read", checked_at: NOW },
      latest_check: { result: "blocked", scan: "a".repeat(32), head_sha: SHA, completed_at: NOW },
      local_hooks: { status: "not_verifiable", detail: "A server cannot see whether developers installed the Git hooks.", checked_at: null },
      monitoring_enabled: true,
    },
    organization_policy_version: 2,
    effective_policies: [{ id: "ai_coauthor", enabled: true, action: "block" }],
    effective_policy_scan: "a".repeat(32),
    recent_scans: [scan()],
    open_violations: [violation()],
    audit: [],
    permissions: { manage: true, trigger_scans: true, read_audit: true },
    ...overrides,
  };
}

export function overview(overrides: Partial<Overview["summary"]> = {}): Overview {
  return {
    period: { key: "7d", start: "2026-08-25T12:00:00Z", end: NOW },
    summary: {
      repositories_monitored: 3,
      repositories_protected: 1,
      repositories_unprotected: 0,
      repositories_unknown: 2,
      repositories_configuration_error: 0,
      scans: 12,
      scans_passed: 10,
      scans_blocked: 2,
      scans_error: 0,
      open_violations: 1,
      open_warnings: 1,
      critical_open: 0,
      high_open: 1,
      ...overrides,
    },
    integration: { status: "connected", installations_connected: 1, installations_suspended: 0, installations_disconnected: 0, detail: "The GitHub App is installed." },
    health: [{ id: "required_check", label: "Merge protection", status: "unknown", detail: "1 of 3 verified." }],
    recent_scans: [scan()],
    recent_violations: [violation()],
    repository_health: [repository()],
  };
}

export function policy(canWrite = true, floors: Record<string, "block" | "warn" | null> = { ai_coauthor: "block" }): OrganizationPolicy {
  const ids = ["ai_coauthor", "bot_identity"];
  return {
    organization: { id: 1001, login: "octo-org", type: "Organization" },
    version: 3,
    fingerprint: "d".repeat(64),
    updated_at: NOW,
    updated_by: { id: 501, login: "alice" },
    reason: "org rule",
    service_policy: null,
    rules: ids.map((id) => ({
      policy_id: id,
      name: id === "ai_coauthor" ? "AI co-author attribution" : "Automation or bot identity",
      description: "desc",
      default_action: id === "ai_coauthor" ? "block" : "warn",
      service_floor: null,
      organization_floor: floors[id] ?? null,
      minimum_action: floors[id] ?? null,
      repository_override: floors[id] ? "stricter_only" : "any",
      source: floors[id] ? "organization_policy" : "built_in_default",
    })),
    can_write: canWrite,
  };
}

export function auditEvent(overrides: Partial<AuditEvent> = {}): AuditEvent {
  return {
    id: "e".repeat(32),
    type: "organization_policy_changed",
    occurred_at: NOW,
    actor: { type: "user", id: 501, login: "alice" },
    organization_id: 1001,
    installation_id: null,
    repository: null,
    head_sha: null,
    action: null,
    summary: "Changed organization policy v2 → v3: ai_coauthor: warn -> block",
    data: {},
    scan: null,
    ...overrides,
  };
}
