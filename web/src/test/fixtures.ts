import type {
  AuditEvent,
  EffectivePolicyView,
  OrganizationPosture,
  PolicyDraft,
  PolicyException,
  RepositoryGroup,
  RepositoryPosture,
  RuleProvenance,
  SettingsView,
  Simulation,
  Execution,
  Finding,
  NotificationItem,
  NotificationSettings,
  OrganizationAccess,
  OrganizationPolicy,
  Overview,
  Permission,
  PolicyVersion,
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

const VIEWER: Permission[] = [
  "repositories:read",
  "scans:read",
  "violations:read",
  "policies:read",
  "rules:read",
  "organization:read",
  "exceptions:read",
  "security:read",
];
const ADMIN: Permission[] = [
  ...VIEWER,
  "notifications:read",
  "violations:manage",
  "scans:trigger",
  "audit:read",
  "policies:write",
  "policies:rollback",
  "repositories:manage",
  "github:manage",
  "members:read",
  "notifications:manage",
  "exceptions:create",
  "policies:publish",
  "policies:approve",
  "organization:manage",
  "rules:manage",
  "exceptions:approve",
  "exceptions:revoke",
  "security:manage",
];

export function organization(role: "viewer" | "admin" | "owner" = "admin"): OrganizationAccess {
  const permissions = role === "viewer" ? VIEWER : role === "owner" ? [...ADMIN, "members:manage" as Permission, "policies:emergency" as Permission] : ADMIN;
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
    trigger: "pull_request",
    execution: 1,
    failure_source: "pull_request",
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
    executions: 1,
    latest_execution: "a".repeat(32),
    merge_group: null,
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
      merge_queue: { status: "unknown", detail: "merge queue settings could not be read", checked_at: NOW },
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
      repositories_at_risk: 0,
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
      organization_default: null,
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

export function policyVersion(overrides: Partial<PolicyVersion> = {}): PolicyVersion {
  return {
    version: 2,
    fingerprint: "e".repeat(64),
    floors: { ai_coauthor: "block" },
    created_at: NOW,
    created_by: { id: 501, login: "alice" },
    reason: "stable",
    status: "archived",
    kind: "change",
    rollback_of: null,
    restored_version: null,
    changes: [],
    summary: "ai_coauthor: repository -> block",
    defaults: {},
    draft_id: null,
    emergency: false,
    ...overrides,
  };
}

export function notification(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "c".repeat(32),
    type: "high_violation",
    category: "violations",
    severity: "high",
    state: "unread",
    title: "Blocked: ai_coauthor in octo-org/payments-api",
    body: "CommitGuard blocked 3 commit(s) in pull request #7 of octo-org/payments-api (ai_coauthor, high).",
    organization_id: 1001,
    repository: { id: 5001, installation_id: 42, full_name: "octo-org/payments-api" },
    resource_type: "violation",
    resource_id: "b".repeat(32),
    link: `/violations/${"b".repeat(32)}`,
    occurrences: 1,
    created_at: NOW,
    last_occurred_at: NOW,
    read_at: null,
    ...overrides,
  };
}

export function execution(overrides: Partial<Execution> = {}): Execution {
  return {
    id: "a".repeat(32),
    execution: 1,
    trigger: "pull_request",
    current: false,
    result: "blocked",
    head_sha: SHA,
    base_sha: BASE_SHA,
    organization_policy_version: 1,
    policy_version: "ef334cbde9255add",
    rules_version: "57b1df750182531f",
    tool_version: "0.1.0.dev0",
    conclusion: "failure",
    requested_by: null,
    failure: null,
    created_at: NOW,
    started_at: NOW,
    completed_at: "2026-09-01T12:00:02Z",
    duration_ms: 2000,
    ...overrides,
  };
}

export function notificationSettings(canManage = true, overrides: Partial<NotificationSettings> = {}): NotificationSettings {
  const type = (name: string, label: string, mandatory: boolean, email: boolean, webhook: boolean) => ({
    type: name,
    label,
    description: `${label} description`,
    category: "violations" as const,
    mandatory_in_app: mandatory,
    organization: { in_app: true, email, webhook },
    personal_in_app: true,
    receives_in_app: true,
  });
  return {
    organization: { id: 1001, login: "octo-org", type: "Organization" },
    version: 1,
    updated_at: NOW,
    updated_by: "ada",
    channels: { in_app: true, email: true, webhook: true, mode: "deliver" },
    types: [type("critical_violation", "Critical violations", true, true, true), type("high_violation", "High-severity violations", false, false, false)],
    email_recipients: canManage ? ["security@example.com"] : [],
    webhooks: canManage ? [{ id: "d".repeat(32), url: "https://hooks.example.com/commitguard", created_at: NOW, created_by: "ada" }] : [],
    can_manage: canManage,
    ...overrides,
  };
}

/* Organization governance ------------------------------------------------ */

export const GROUP_ID = "1".repeat(32);
export const DRAFT_ID = "2".repeat(32);
export const EXCEPTION_ID = "3".repeat(32);

export function organizationPosture(overrides: Partial<OrganizationPosture> = {}): OrganizationPosture {
  return {
    organization_id: 1001,
    login: "octo-org",
    type: "Organization",
    github_url: "https://github.com/octo-org",
    posture: "at_risk",
    posture_reasons: ["1 repository(ies) at risk.", "Installation octo-org (42): The last synchronisation did not finish."],
    members: 4,
    repositories: 3,
    required_repositories: 3,
    compliant_repositories: 2,
    compliance: "2 of 3 required repositories satisfy all mandatory controls",
    by_posture: { at_risk: 1, unprotected: 0, unknown: 0, secure: 2 },
    by_protection: { protected: 2, at_risk: 0, unprotected: 0, configuration_error: 0, unknown: 1 },
    monitor_mode: 1,
    critical_open: 1,
    high_open: 2,
    active_exceptions: 1,
    expiring_exceptions: 1,
    expired_exceptions_30d: 0,
    drift: { compliant: 1, customized: 1, drift: 1, unknown: 0 },
    installations: [{ installation_id: 42, account_login: "octo-org", state: "active", sync: "failed", sync_detail: "The last synchronisation did not finish.", last_success_at: NOW, repositories: 3 }],
    policy: { organization_version: 3, updated_at: NOW, updated_by: "alice", approvals_pending: 1, exceptions_requested: 1, rollouts_in_progress: 0, propagation: { up_to_date: 3 }, baseline: {} },
    recent_activity: [{ id: "e".repeat(32), type: "policy_published", occurred_at: NOW, actor: "alice" }],
    computed_at: NOW,
    ...overrides,
  };
}

export function repositoryPosture(overrides: Partial<RepositoryPosture> = {}): RepositoryPosture {
  return {
    repository_id: 5001,
    full_name: "octo-org/payments-api",
    github_url: "https://github.com/octo-org/payments-api",
    installation_id: 42,
    groups: [{ id: GROUP_ID, name: "Production" }],
    connection: "connected",
    archived: false,
    onboarding: "onboarded",
    mode: "enforce",
    protection: "protected",
    protection_reason: "GitHub requires the CommitGuard check.",
    posture: "secure",
    posture_reasons: ["Protected, enforcing, no open critical violations."],
    organization_policy_version: 3,
    policy_state: "up_to_date",
    last_scan_result: "pass",
    last_scan_at: NOW,
    open_violations: 0,
    open_warnings: 0,
    critical_open: 0,
    active_exceptions: 0,
    expiring_exceptions: 0,
    drift: "compliant",
    drift_differences: [],
    ...overrides,
  };
}

export function group(overrides: Partial<RepositoryGroup> = {}): RepositoryGroup {
  return {
    id: GROUP_ID,
    organization_id: 1001,
    name: "Production",
    description: "Customer-facing services",
    repository_count: 2,
    policy_version: 1,
    active_exceptions: 0,
    created_at: NOW,
    created_by: "alice",
    updated_at: NOW,
    archived_at: null,
    ...overrides,
  };
}

export function draft(overrides: Partial<PolicyDraft> = {}): PolicyDraft {
  return {
    id: DRAFT_ID,
    organization_id: 1001,
    target: { type: "organization", id: "", label: "Organization" },
    title: "Block AI attribution trailers everywhere",
    reason: "Generated-by trailers are attribution too",
    state: "pending_approval",
    revision: 1,
    base_version: 3,
    current_version: 3,
    floors: { ai_coauthor: "block", ai_trailer: "block" },
    defaults: {},
    changes: [{ policy_id: "ai_trailer", old: null, new: "block", weakening: false, enforcement: "mandatory" }],
    diff: { from_version: 3, to_version: 4, added: [], changed: [], removed: [], weakening: false },
    weakening: false,
    rebase_required: false,
    requires_approval: true,
    created_by: "alice",
    created_at: NOW,
    updated_at: NOW,
    submitted_by: "alice",
    submitted_at: NOW,
    published_version: null,
    published_at: null,
    published_by: null,
    emergency: false,
    rollout_id: null,
    approvals: [{ id: "4".repeat(32), status: "pending", requested_by: "alice", requested_at: NOW, decided_by: null, decided_at: null, reason: null }],
    can_edit: false,
    can_submit: false,
    can_approve: false,
    can_publish: false,
    ...overrides,
  };
}

export function simulation(overrides: Partial<Simulation> = {}): Simulation {
  return {
    id: "5".repeat(32),
    organization_id: 1001,
    target: { type: "organization", id: "", label: "Organization" },
    draft_id: DRAFT_ID,
    current_version: 3,
    state: "completed",
    parameters: { period_days: 30, repository_ids: null },
    requested_by: "ada",
    requested_at: NOW,
    started_at: NOW,
    completed_at: NOW,
    result: {
      repositories_analyzed: 3,
      repositories_without_data: 1,
      scans_analyzed: 12,
      findings_analyzed: 9,
      new_blocks: 2,
      new_warnings: 0,
      no_longer_blocked: 0,
      unchanged: 7,
      scans_newly_blocked: 1,
      scans_no_longer_blocked: 0,
      scans_assumed_defaults: 4,
      most_affected: [{ repository_id: 5001, full_name: "octo-org/payments-api", scans: 5, new_blocks: 2, new_warnings: 0, no_longer_blocked: 0 }],
      truncated: false,
      disclaimer: "SIMULATION - an estimate from recorded scans in the selected period, not the current security state and not a prediction of future commits.",
    },
    error: null,
    ...overrides,
  };
}

export function policyException(overrides: Partial<PolicyException> = {}): PolicyException {
  return {
    id: EXCEPTION_ID,
    organization_id: 1001,
    rule_id: "malformed_trailer",
    rule_name: "Malformed trailer",
    severity: "low",
    scope: { type: "repository", id: "5003", label: "octo-org/engineering-handbook" },
    action: "allow",
    reason: "The handbook generator writes non-standard trailers",
    status: "active",
    requires_approval: false,
    permanent: false,
    expires_at: "2026-09-05T12:00:00Z",
    expiring_soon: true,
    requested_at: NOW,
    requested_by: "alice",
    decided_at: null,
    decided_by: null,
    decision_note: null,
    activated_at: NOW,
    revoked_at: null,
    revoked_by: null,
    revoke_reason: null,
    expired_at: null,
    can_approve: false,
    can_revoke: true,
    can_cancel: false,
    ...overrides,
  };
}

export function settingsView(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    organization_id: 1001,
    version: 2,
    settings: {
      security_baseline: {},
      require_policy_approval: true,
      require_separate_approver: true,
      exception_approval_min_severity: "high",
      exception_max_days: 90,
      allow_permanent_exceptions: false,
      exception_warning_days: [7, 3, 1],
      default_onboarding_mode: "enforce",
      auto_onboard_new_repositories: true,
      archived_repositories: "keep",
      rollout_auto_pause: true,
      rollout_max_error_rate: 0.2,
      rollout_max_block_rate: 0.5,
      rollout_min_scans: 5,
      rollout_auto_rollback: false,
      aggregate_violation_alerts: false,
      timezone: "UTC",
    },
    updated_at: NOW,
    updated_by: "alice",
    can_manage: true,
    ...overrides,
  };
}

export function provenance(overrides: Partial<RuleProvenance> = {}): RuleProvenance {
  return {
    policy_id: "ai_coauthor",
    enabled: true,
    action: "block",
    source: "organization",
    source_label: "organization policy v3",
    enforcement: "mandatory",
    required_action: "block",
    required_by: "organization",
    required_label: "organization policy v3",
    conflict: null,
    exception_id: null,
    exception_expires_at: null,
    action_before_exception: null,
    monitor_mode: false,
    repository_configuration_known: true,
    ...overrides,
  };
}

export function effectivePolicy(overrides: Partial<EffectivePolicyView> = {}): EffectivePolicyView {
  const rules = [provenance()];
  return {
    organization_id: 1001,
    repository_id: 5001,
    full_name: "octo-org/payments-api",
    mode: "enforce",
    effective: { policies: [{ id: "ai_coauthor", enabled: true, action: "block", description: "AI co-author" }], rules, inputs_fingerprint: "f".repeat(64), description: "organization policy v3", mode: "enforce" },
    versions: { organization_policy: 3, settings: 2, groups: { [GROUP_ID]: 1 }, repository_policy: null, rollouts: {}, exceptions: [], organization_rules: null },
    propagation: "up_to_date",
    resolved_at: NOW,
    last_scan_id: "a".repeat(32),
    last_scan_completed_at: NOW,
    last_scan_effective: null,
    last_scan_used_current_policy: true,
    ...overrides,
  };
}
