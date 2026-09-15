/**
 * API contract types. These mirror `commitguard.controlplane.views` exactly;
 * every status is a server-decided value from a closed vocabulary that the
 * dashboard renders and never derives itself.
 */

export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type PolicyAction = "allow" | "warn" | "block";
export type ScanResult =
  | "queued"
  | "running"
  | "pass"
  | "warning"
  | "blocked"
  | "error"
  | "cancelled";
export type ViolationStatus = "open" | "acknowledged" | "resolved";
export type ProtectionStatus = "protected" | "unprotected" | "configuration_error" | "unknown";
export type AppConnection = "connected" | "suspended" | "disconnected";
export type ActionsStatus = "detected" | "not_detected" | "unknown";
export type RequiredCheckStatus = "required" | "not_required" | "unknown";
export type IntegrationStatus = "connected" | "action_required" | "disconnected";
export type HealthCheckStatus = "ok" | "attention" | "unknown";
export type Role = "viewer" | "security_manager" | "admin" | "owner";
export type Permission =
  | "repositories:read"
  | "repositories:manage"
  | "scans:read"
  | "scans:trigger"
  | "violations:read"
  | "violations:manage"
  | "policies:read"
  | "policies:write"
  | "rules:read"
  | "audit:read"
  | "github:manage"
  | "members:read"
  | "members:manage";

export interface Envelope<T> {
  data: T;
  meta: PageMeta & Record<string, unknown>;
}

export interface PageMeta {
  next_cursor?: string | null;
  limit?: number;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
  limit: number;
}

export interface OrganizationRef {
  id: number;
  login: string;
  type: string;
}

export interface RepositoryLink {
  id: number;
  installation_id: number;
  full_name: string;
}

export interface Actor {
  type: "system" | "github" | "user";
  id: number | null;
  login: string | null;
}

export interface PolicyEntry {
  id: string;
  enabled: boolean;
  action: PolicyAction;
}

export interface ScanSummary {
  id: string;
  scan_id: string | null;
  repository: RepositoryLink;
  organization_id: number | null;
  event: string;
  pull_request_number: number | null;
  ref: string | null;
  base_sha: string | null;
  head_sha: string;
  check_name: string;
  result: ScanResult;
  commits_scanned: number | null;
  violations: number | null;
  warnings: number | null;
  findings: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  requested_by: string | null;
}

export interface Match {
  kind: string;
  value: string;
  rule: string;
}

export interface Evidence {
  source: string;
  source_label: string;
  value: string;
  line_number: number | null;
  matched: Match[];
  notes: string[];
}

export interface Finding {
  id: number;
  violation_id: string | null;
  rule_id: string;
  detector: string;
  title: string;
  message: string;
  severity: Severity;
  confidence: string;
  action: PolicyAction;
  reason: string;
  commit_sha: string | null;
  author: string | null;
  committer: string | null;
  evidence: Evidence[];
  remediation: string;
}

export interface ScanDetail {
  scan: ScanSummary;
  conclusion: string | null;
  tool_version: string | null;
  rules_version: string | null;
  policy_version: string | null;
  policy_source: string | null;
  organization_policy_version: number | null;
  effective_policies: PolicyEntry[];
  detector_failures: number | null;
  notices: string[];
  failure: { kind: string | null; message: string } | null;
  findings: Finding[];
  can_rescan: boolean;
  rescan_blocked_reason: string | null;
}

export interface ScanComparison {
  scan_id: string;
  previous_scan_id: string | null;
  new: string[];
  resolved: string[];
  unchanged: string[];
  new_findings: Finding[];
  resolved_findings: Finding[];
}

export interface ViolationSummary {
  id: string;
  rule_id: string;
  title: string;
  severity: Severity;
  action: PolicyAction;
  repository: RepositoryLink;
  organization_id: number | null;
  commit_sha: string | null;
  author: string | null;
  status: ViolationStatus;
  first_detected_at: string;
  last_detected_at: string;
  detections: number;
}

export interface Exposure {
  kind: "pull_request" | "branch";
  label: string;
  active: boolean;
  opened_at: string;
  closed_at: string | null;
  closed_reason: string | null;
}

export interface Detection {
  scan: string;
  result: ScanResult;
  detected_at: string;
  action: PolicyAction;
  head_sha: string;
}

export interface ViolationDetail {
  violation: ViolationSummary;
  detector: string;
  message: string;
  committer: string | null;
  policy_reason: string;
  evidence: Evidence[];
  remediation: string;
  recommended_steps: string[];
  resolved_at: string | null;
  resolution: string | null;
  acknowledgement: { by: string | null; at: string; note: string | null } | null;
  exposures: Exposure[];
  detections: Detection[];
  first_scan_id: string;
  last_scan_id: string;
  can_manage: boolean;
}

export interface EnforcementSignal {
  status: string;
  detail: string;
  checked_at: string | null;
}

export interface RequiredCheckSignal {
  status: RequiredCheckStatus;
  branch: string | null;
  required_checks: string[];
  detail: string;
  checked_at: string | null;
}

export interface Enforcement {
  github_app: EnforcementSignal;
  github_actions: EnforcementSignal;
  required_check: RequiredCheckSignal;
  latest_check: {
    result: ScanResult | null;
    scan: string | null;
    head_sha: string | null;
    completed_at: string | null;
  };
  local_hooks: EnforcementSignal;
  monitoring_enabled: boolean;
}

export interface RepositorySummary {
  id: number;
  installation_id: number;
  organization: OrganizationRef | null;
  owner: string;
  name: string;
  full_name: string;
  github_url: string;
  default_branch: string | null;
  protection: ProtectionStatus;
  protection_reason: string;
  app_connection: AppConnection;
  monitoring_enabled: boolean;
  last_scan: ScanSummary | null;
  open_violations: number;
  open_warnings: number;
  critical_open: number;
}

export interface RepositoryDetail {
  repository: RepositorySummary;
  enforcement: Enforcement;
  organization_policy_version: number | null;
  effective_policies: PolicyEntry[];
  effective_policy_scan: string | null;
  recent_scans: ScanSummary[];
  open_violations: ViolationSummary[];
  audit: AuditEvent[];
  permissions: { manage: boolean; trigger_scans: boolean; read_audit: boolean };
}

export interface PolicyRule {
  policy_id: string;
  name: string;
  description: string;
  default_action: PolicyAction;
  service_floor: PolicyAction | null;
  organization_floor: PolicyAction | null;
  minimum_action: PolicyAction | null;
  repository_override: "any" | "stricter_only";
  source: "built_in_default" | "service_policy" | "organization_policy";
}

export interface OrganizationPolicy {
  organization: OrganizationRef;
  version: number;
  fingerprint: string | null;
  updated_at: string | null;
  updated_by: { id: number | null; login: string | null } | null;
  reason: string | null;
  service_policy: string | null;
  rules: PolicyRule[];
  can_write: boolean;
}

export interface PolicyVersion {
  version: number;
  fingerprint: string;
  floors: Record<string, PolicyAction>;
  created_at: string;
  created_by: { id: number | null; login: string | null };
  reason: string | null;
}

export interface PolicyChange {
  policy_id: string;
  old: PolicyAction | null;
  new: PolicyAction | null;
  weakening: boolean;
}

export interface PolicyPreview {
  version: number;
  changes: PolicyChange[];
  weakening: boolean;
}

export interface Rule {
  id: string;
  name: string;
  description: string;
  detector: string;
  severity: Severity;
  default_action: PolicyAction;
  source: "bundled";
  trusted: boolean;
  status: "active";
  rules_version: string;
  tool_version: string;
}

export interface RuleDetail {
  rule: Rule;
  remediation: string[];
  evidence_sources: string[];
  data_files: { name: string; entries: number }[];
  editable: boolean;
}

export interface AuditEvent {
  id: string;
  type: string;
  occurred_at: string;
  actor: Actor;
  organization_id: number | null;
  installation_id: number | null;
  repository: RepositoryLink | null;
  head_sha: string | null;
  action: PolicyAction | null;
  summary: string;
  data: Record<string, string | number | boolean | null>;
  scan: string | null;
}

export interface Installation {
  id: number;
  account: OrganizationRef;
  status: AppConnection;
  repository_selection: string;
  repositories: number;
  permissions: Record<string, string>;
  missing_permissions: string[];
  excessive_permissions: string[];
  installed_at: string;
  updated_at: string;
  last_event_at: string | null;
  github_settings_url: string;
  can_manage: boolean;
}

export interface InstallationDetail {
  installation: Installation;
  recent_events: AuditEvent[];
}

export interface InstallationRepository {
  id: number;
  full_name: string;
  connected: boolean;
  monitoring_enabled: boolean;
  added_at: string | null;
  removed_at: string | null;
}

export interface SyncResult {
  installation_id: number;
  repositories: number;
  added: string[];
  removed: string[];
  synced_at: string;
}

export interface Overview {
  period: { key: "24h" | "7d" | "30d"; start: string; end: string };
  summary: {
    repositories_monitored: number;
    repositories_protected: number;
    repositories_unprotected: number;
    repositories_unknown: number;
    repositories_configuration_error: number;
    scans: number;
    scans_passed: number;
    scans_blocked: number;
    scans_error: number;
    open_violations: number;
    open_warnings: number;
    critical_open: number;
    high_open: number;
  };
  integration: {
    status: IntegrationStatus;
    installations_connected: number;
    installations_suspended: number;
    installations_disconnected: number;
    detail: string;
  };
  health: { id: string; label: string; status: HealthCheckStatus; detail: string }[];
  recent_scans: ScanSummary[];
  recent_violations: ViolationSummary[];
  repository_health: RepositorySummary[];
}

export interface OrganizationAccess {
  organization: OrganizationRef;
  role: Role;
  implicit_role: boolean;
  permissions: Permission[];
  installation_ids: number[];
}

export interface Member {
  user_id: number;
  login: string | null;
  role: Role;
  granted_by: string;
  created_at: string;
  updated_at: string;
  implicit: boolean;
}

export interface SessionView {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  user_agent: string;
  current: boolean;
}

export interface SessionInfo {
  user: { id: number; login: string };
  session: SessionView;
  csrf_token: string;
  organizations: OrganizationAccess[];
  reauthentication_required_after: string;
}
