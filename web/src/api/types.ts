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
  | "cancelled"
  | "stale";
export type ScanTrigger = "push" | "pull_request" | "merge_group" | "manual" | "rerun" | "retry";
export type ViolationStatus = "open" | "acknowledged" | "resolved";
export type ProtectionStatus = "protected" | "at_risk" | "unprotected" | "configuration_error" | "unknown";
export type MergeQueueStatus = "enabled" | "not_enabled" | "unknown";
export type NotificationState = "unread" | "read" | "archived";
export type NotificationCategory = "violations" | "policy" | "github" | "scans";
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
  | "members:manage"
  | "policies:rollback"
  | "notifications:read"
  | "notifications:manage"
  | "organization:read"
  | "organization:manage"
  | "policies:publish"
  | "policies:approve"
  | "policies:emergency"
  | "rules:manage"
  | "exceptions:read"
  | "exceptions:create"
  | "exceptions:approve"
  | "exceptions:revoke"
  | "security:read"
  | "security:manage";

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
  trigger: ScanTrigger;
  execution: number;
  failure_source: "pull_request" | "push" | "merge_queue";
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
  executions: number;
  latest_execution: string;
  merge_group: MergeGroup | null;
}

export interface Execution {
  id: string;
  execution: number;
  trigger: ScanTrigger;
  current: boolean;
  result: ScanResult;
  head_sha: string;
  base_sha: string | null;
  organization_policy_version: number | null;
  policy_version: string | null;
  rules_version: string | null;
  tool_version: string | null;
  conclusion: string | null;
  requested_by: string | null;
  failure: { kind: string | null; message: string } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface ExecutionHistory {
  scan_id: string;
  items: Execution[];
  policy_changed: boolean;
  rules_changed: boolean;
}

export interface MergeGroup {
  head_sha: string;
  base_sha: string;
  base_ref: string;
  pull_requests: number[];
  state: "checks_requested" | "destroyed";
  destroyed_reason: string | null;
  result: ScanResult | null;
  scan: string | null;
  created_at: string;
  updated_at: string;
  validated_at: string | null;
}

export interface MergeQueue {
  repository_id: number;
  status: MergeQueueStatus;
  detail: string;
  checked_at: string | null;
  permission: "granted" | "missing";
  current: MergeGroup | null;
  recent: MergeGroup[];
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
  kind: "pull_request" | "branch" | "merge_group";
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
  merge_queue: EnforcementSignal;
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
  source: "built_in_default" | "service_policy" | "organization_policy" | "organization_default";
  /** A default-strength organization entry: the baseline repositories may change. */
  organization_default: PolicyAction | null;
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

export interface PolicyChange {
  policy_id: string;
  old: PolicyAction | null;
  new: PolicyAction | null;
  weakening: boolean;
  enforcement: PolicyStrength;
}

export interface PolicyVersion {
  version: number;
  fingerprint: string;
  floors: Record<string, PolicyAction>;
  created_at: string;
  created_by: { id: number | null; login: string | null };
  reason: string | null;
  status: "active" | "archived";
  kind: "change" | "rollback";
  rollback_of: number | null;
  restored_version: number | null;
  changes: PolicyChange[];
  summary: string;
  /** Default-strength entries (organization governance). */
  defaults: Record<string, PolicyAction>;
  /** The reviewed draft this version was published from. */
  draft_id: string | null;
  /** Published without the approval workflow. */
  emergency: boolean;
}

export interface PolicyDiffEntry {
  policy_id: string;
  old: PolicyAction | null;
  new: PolicyAction | null;
  weakening: boolean;
  enforcement: PolicyStrength;
}

export interface PolicyDiff {
  from_version: number;
  to_version: number;
  added: PolicyDiffEntry[];
  changed: PolicyDiffEntry[];
  removed: PolicyDiffEntry[];
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
    repositories_at_risk: number;
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

export interface NotificationItem {
  id: string;
  type: string;
  category: NotificationCategory;
  severity: Severity;
  state: NotificationState;
  title: string;
  body: string;
  organization_id: number;
  repository: RepositoryLink | null;
  resource_type: string;
  resource_id: string;
  link: string | null;
  occurrences: number;
  created_at: string;
  last_occurred_at: string;
  read_at: string | null;
}

export interface NotificationCounts {
  unread: number;
  unread_critical: number;
  capped: boolean;
}

export interface ChannelPreference {
  in_app: boolean;
  email: boolean;
  webhook: boolean;
}

export interface TypePreference {
  type: string;
  label: string;
  description: string;
  category: NotificationCategory;
  mandatory_in_app: boolean;
  organization: ChannelPreference;
  personal_in_app: boolean;
  receives_in_app: boolean;
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  created_at: string;
  created_by: string | null;
}

export interface NotificationSettings {
  organization: OrganizationRef;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  channels: { in_app: boolean; email: boolean; webhook: boolean; mode: "off" | "deliver" | "test" };
  types: TypePreference[];
  email_recipients: string[];
  webhooks: WebhookEndpoint[];
  can_manage: boolean;
}

export interface NotificationDelivery {
  id: string;
  notification_type: string;
  title: string;
  channel: "email" | "webhook";
  destination: string;
  status: "pending" | "sent" | "failed" | "cancelled";
  attempts: number;
  failure_code: string | null;
  last_attempt_at: string | null;
  next_retry_at: string | null;
  created_at: string;
}

export interface CreatedWebhook {
  endpoint: WebhookEndpoint;
  signing_secret: string;
}

/* ------------------------------------------------------------------------ */
/* Organization governance. These mirror `commitguard.governance.*`,        */
/* `commitguard.policies.governance` and the scoped policy views; posture,  */
/* compliance, drift and propagation are decided on the server.             */
/* ------------------------------------------------------------------------ */

export type PolicyStrength = "mandatory" | "default";
export type PolicyTargetType = "organization" | "group" | "repository";
export type Posture = "secure" | "at_risk" | "unprotected" | "unknown";
export type RepositoryMode = "enforce" | "monitor";
export type OnboardingState = "discovered" | "onboarded" | "excluded";
export type PropagationState = "up_to_date" | "stale" | "syncing" | "error" | "pending";
export type DriftState = "compliant" | "customized" | "drift" | "unknown";
export type SyncHealth = "healthy" | "syncing" | "degraded" | "failed" | "never";
export type PolicyLevel =
  | "built_in"
  | "service"
  | "organization"
  | "group"
  | "repository_policy"
  | "repository_configuration"
  | "exception"
  | "monitor_mode";
export type DraftState = "draft" | "pending_approval" | "approved" | "rejected" | "cancelled" | "published";
export type ExceptionStatus = "requested" | "active" | "rejected" | "cancelled" | "revoked" | "expired";
export type RolloutState = "pilot" | "rollout" | "active" | "paused" | "rolled_back";
export type SimulationState = "queued" | "running" | "completed" | "failed";
export type BulkOperationType = "add_to_group" | "remove_from_group" | "onboard" | "set_mode" | "set_monitoring" | "schedule_scan";
export type BulkStatus = "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
export type ReportKind = "compliance" | "coverage" | "violations" | "exceptions" | "policy_changes" | "installations";

/** `commitguard.governance.settings.OrganizationSettings` */
export interface OrganizationSettings {
  security_baseline: Record<string, PolicyAction>;
  require_policy_approval: boolean;
  require_separate_approver: boolean;
  exception_approval_min_severity: Severity;
  exception_max_days: number;
  allow_permanent_exceptions: boolean;
  exception_warning_days: number[];
  default_onboarding_mode: RepositoryMode;
  auto_onboard_new_repositories: boolean;
  archived_repositories: "keep" | "exclude";
  rollout_auto_pause: boolean;
  rollout_max_error_rate: number;
  rollout_max_block_rate: number;
  rollout_min_scans: number;
  rollout_auto_rollback: boolean;
  aggregate_violation_alerts: boolean;
  timezone: string;
}

export interface SettingsView {
  organization_id: number;
  /** 0: organization defaults, never saved. */
  version: number;
  settings: OrganizationSettings;
  updated_at: string | null;
  updated_by: string | null;
  can_manage: boolean;
}

export interface InstallationHealth {
  installation_id: number;
  account_login: string;
  state: "active" | "suspended" | "deleted" | string;
  sync: SyncHealth;
  sync_detail: string;
  last_success_at: string | null;
  repositories: number;
}

export interface OrganizationPolicyStatus {
  organization_version: number;
  updated_at: string | null;
  updated_by: string | null;
  approvals_pending: number;
  exceptions_requested: number;
  rollouts_in_progress: number;
  propagation: Partial<Record<PropagationState, number>>;
  baseline: Record<string, PolicyAction>;
}

export interface ActivityItem {
  id: string;
  type: string;
  occurred_at: string;
  actor: string | null;
}

/** `commitguard.governance.posture.OrganizationPostureView` */
export interface OrganizationPosture {
  organization_id: number;
  login: string;
  type: string;
  github_url: string;
  posture: Posture;
  posture_reasons: string[];
  members: number;
  repositories: number;
  required_repositories: number;
  compliant_repositories: number;
  /** The server's sentence, e.g. "94 of 100 required repositories satisfy all mandatory controls". */
  compliance: string;
  by_posture: Record<Posture, number>;
  by_protection: Partial<Record<ProtectionStatus, number>>;
  monitor_mode: number;
  critical_open: number;
  high_open: number;
  active_exceptions: number;
  expiring_exceptions: number;
  expired_exceptions_30d: number;
  drift: Record<DriftState, number>;
  installations: InstallationHealth[];
  policy: OrganizationPolicyStatus;
  recent_activity: ActivityItem[];
  computed_at: string;
}

export interface OrganizationDetail {
  organization: OrganizationPosture;
  settings: SettingsView;
}

export interface DriftDifference {
  policy_id: string;
  requested: PolicyAction | "disabled";
  requested_by: string;
  required: PolicyAction;
  required_by: string;
  effective: PolicyAction;
}

export interface GroupRef {
  id: string;
  name: string;
}

/** One row of the repository security matrix. */
export interface RepositoryPosture {
  repository_id: number;
  full_name: string;
  github_url: string;
  installation_id: number;
  groups: GroupRef[];
  connection: AppConnection;
  archived: boolean;
  onboarding: OnboardingState;
  mode: RepositoryMode;
  protection: ProtectionStatus;
  protection_reason: string;
  posture: Posture;
  posture_reasons: string[];
  organization_policy_version: number | null;
  policy_state: PropagationState;
  last_scan_result: ScanResult | null;
  last_scan_at: string | null;
  open_violations: number;
  open_warnings: number;
  critical_open: number;
  active_exceptions: number;
  expiring_exceptions: number;
  drift: DriftState;
  drift_differences: DriftDifference[];
}

export interface MatrixPage extends Page<RepositoryPosture> {
  total: number;
  computedAt: string | null;
}

export interface TrendPoint {
  day: string;
  values: Record<string, number>;
}

export interface TrendsView {
  organization_id: number;
  days: number;
  history: TrendPoint[];
  snapshots: TrendPoint[];
  snapshot_note: string;
  computed_at: string;
}

export interface SecurityEvent {
  id: string;
  type: string;
  severity: Severity;
  title: string;
  body: string;
  occurrences: number;
  last_occurred_at: string;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface Acknowledgement {
  event_id: string;
  acknowledged_by: string;
  acknowledged_at: string;
  meaning: string;
}

export interface SearchResult {
  kind: "repository" | "group" | "policy" | "rule" | "exception" | "finding" | string;
  id: string;
  title: string;
  detail: string;
  link: string;
}

/* Repository groups ------------------------------------------------------ */
export interface RepositoryGroup {
  id: string;
  organization_id: number;
  name: string;
  description: string | null;
  repository_count: number;
  policy_version: number;
  active_exceptions: number;
  created_at: string;
  created_by: string | null;
  updated_at: string;
  archived_at: string | null;
}

export interface GroupMember {
  repository_id: number;
  full_name: string;
  added_at: string;
  added_by: string | null;
}

export interface RepositoryGroupDetail {
  group: RepositoryGroup;
  repositories: GroupMember[];
  /** Members GitHub did not report to this session. */
  hidden_repositories: number;
  can_manage: boolean;
}

/* Scoped policies, drafts, approvals ------------------------------------ */
export interface PolicyTarget {
  type: PolicyTargetType;
  /** "" for the organization, a group ID or a repository ID. */
  id: string;
  label: string;
}

export interface ScopedRule {
  policy_id: string;
  name: string;
  mandatory: PolicyAction | null;
  default: PolicyAction | null;
}

export interface ScopedPolicy {
  organization: OrganizationRef;
  target: PolicyTarget;
  /** 0: nothing published yet. */
  version: number;
  fingerprint: string | null;
  updated_at: string | null;
  updated_by: { id: number | null; login: string | null } | null;
  reason: string | null;
  rules: ScopedRule[];
  can_write: boolean;
}

export interface PolicyTargets {
  organization: OrganizationPolicy;
  groups: ScopedPolicy[];
  repositories: ScopedPolicy[];
}

export interface TargetVersions {
  policy: OrganizationPolicy | ScopedPolicy;
  versions: PolicyVersion[];
}

export interface PolicyApproval {
  id: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | string;
  requested_by: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  reason: string | null;
}

/** `commitguard.governance.workflow.PolicyDraftView` */
export interface PolicyDraft {
  id: string;
  organization_id: number;
  target: PolicyTarget;
  title: string;
  reason: string | null;
  state: DraftState;
  revision: number;
  base_version: number;
  /** The target's published version right now. */
  current_version: number;
  floors: Record<string, PolicyAction>;
  defaults: Record<string, PolicyAction>;
  changes: PolicyChange[];
  diff: PolicyDiff;
  weakening: boolean;
  rebase_required: boolean;
  requires_approval: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  submitted_by: string | null;
  submitted_at: string | null;
  published_version: number | null;
  published_at: string | null;
  published_by: string | null;
  emergency: boolean;
  rollout_id: string | null;
  approvals: PolicyApproval[];
  can_edit: boolean;
  can_submit: boolean;
  can_approve: boolean;
  can_publish: boolean;
}

/* Simulations ------------------------------------------------------------ */
export interface RepositoryImpact {
  repository_id: number;
  /** null: not visible to the caller. */
  full_name: string | null;
  scans: number;
  new_blocks: number;
  new_warnings: number;
  no_longer_blocked: number;
}

export interface SimulationResult {
  repositories_analyzed: number;
  repositories_without_data: number;
  scans_analyzed: number;
  findings_analyzed: number;
  new_blocks: number;
  new_warnings: number;
  no_longer_blocked: number;
  unchanged: number;
  scans_newly_blocked: number;
  scans_no_longer_blocked: number;
  /** Scans without recorded repository configuration: built-in defaults were assumed. */
  scans_assumed_defaults: number;
  most_affected: RepositoryImpact[];
  truncated: boolean;
  disclaimer: string;
}

export interface Simulation {
  id: string;
  organization_id: number;
  target: PolicyTarget;
  draft_id: string | null;
  current_version: number;
  state: SimulationState;
  parameters: { period_days?: number; repository_ids?: number[] | null } & Record<string, unknown>;
  requested_by: string | null;
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
  result: SimulationResult | null;
  error: string | null;
}

/* Rollouts and propagation ---------------------------------------------- */
export interface RolloutStage {
  index: number;
  name: string;
  kind: "repositories" | "percent";
  percent: number | null;
  /** Planned size (explicit list) or enrolled so far. */
  repositories: number;
  enrolled: number;
  state: "done" | "current" | "planned";
}

export interface Rollout {
  id: string;
  organization_id: number;
  target: PolicyTarget;
  from_version: number;
  to_version: number;
  state: RolloutState;
  stages: RolloutStage[];
  current_stage: number;
  scope_repositories: number;
  enrolled: number;
  /** Enrolled and effective policy resolved with the new version. */
  propagated: number;
  scanned: number;
  passed: number;
  blocked: number;
  errors: number;
  complete: boolean;
  thresholds: { max_error_rate?: number; max_block_rate?: number; min_scans?: number };
  auto_pause: boolean;
  auto_rollback: boolean;
  paused_reason: string | null;
  created_by: string | null;
  created_at: string;
  stage_started_at: string;
  completed_at: string | null;
  rolled_back_at: string | null;
  rollback_version: number | null;
  can_manage: boolean;
}

export interface RolloutRequest {
  stages: ({ name?: string; repositories: number[] } | { name?: string; percent: number })[];
  thresholds?: { max_error_rate?: number; max_block_rate?: number; min_scans?: number };
  auto_pause?: boolean;
  auto_rollback?: boolean;
}

export interface PropagationStatus {
  organization_id: number;
  repositories: number;
  up_to_date: number;
  stale: number;
  syncing: number;
  error: number;
  /** Never resolved yet. */
  pending: number;
  /** Every repository up to date. */
  complete: boolean;
  failing: { repository_id: number; full_name: string; error: string | null }[];
  checked_at: string;
}

export interface SecurityPolicies {
  targets: PolicyTargets;
  drafts: PolicyDraft[];
  rollouts: Rollout[];
  propagation: PropagationStatus;
}

/* Effective policy ------------------------------------------------------- */
export interface EffectivePolicyEntry {
  id: string;
  enabled: boolean;
  action: PolicyAction;
  description: string;
}

export interface PolicyConflict {
  policy_id: string;
  requested_action: PolicyAction;
  requested_enabled: boolean;
  requested_by: PolicyLevel;
  requested_label: string;
  required_action: PolicyAction;
  required_by: PolicyLevel;
  required_label: string;
  effective_action: PolicyAction;
  reason: string;
}

export interface RuleProvenance {
  policy_id: string;
  enabled: boolean;
  action: PolicyAction;
  source: PolicyLevel;
  source_label: string;
  enforcement: PolicyStrength | null;
  /** The floor, when a mandatory entry exists. */
  required_action: PolicyAction | null;
  required_by: PolicyLevel | null;
  required_label: string | null;
  conflict: PolicyConflict | null;
  exception_id: string | null;
  exception_expires_at: string | null;
  action_before_exception: PolicyAction | null;
  /** Block reported as warn. */
  monitor_mode: boolean;
  repository_configuration_known: boolean;
}

export interface EffectivePolicy {
  policies: EffectivePolicyEntry[];
  rules: RuleProvenance[];
  inputs_fingerprint: string;
  description: string;
  mode: RepositoryMode;
}

export interface GovernanceVersions {
  organization_policy: number | null;
  settings: number | null;
  groups: Record<string, number>;
  repository_policy: number | null;
  rollouts: Record<string, number>;
  exceptions: string[];
  organization_rules: number | null;
}

/** `commitguard.governance.resolver.EffectivePolicyView` */
export interface EffectivePolicyView {
  organization_id: number;
  repository_id: number;
  full_name: string;
  mode: RepositoryMode;
  effective: EffectivePolicy;
  versions: GovernanceVersions;
  propagation: PropagationState;
  resolved_at: string;
  last_scan_id: string | null;
  last_scan_completed_at: string | null;
  /** Conflicts involving the repository's own .commitguard.yaml are only known at scan time. */
  last_scan_effective: EffectivePolicy | null;
  last_scan_used_current_policy: boolean | null;
}

/* Exceptions ------------------------------------------------------------- */
export interface PolicyException {
  id: string;
  organization_id: number;
  rule_id: string;
  rule_name: string;
  severity: Severity;
  scope: { type: PolicyTargetType; id: string; label: string };
  action: PolicyAction;
  reason: string;
  status: ExceptionStatus;
  requires_approval: boolean;
  permanent: boolean;
  expires_at: string | null;
  expiring_soon: boolean;
  requested_at: string;
  requested_by: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  activated_at: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  revoke_reason: string | null;
  expired_at: string | null;
  can_approve: boolean;
  can_revoke: boolean;
  can_cancel: boolean;
}

export interface SecurityExceptions {
  counts: Partial<Record<ExceptionStatus | "expiring_soon", number>>;
  exceptions: PolicyException[];
}

/* Bulk operations -------------------------------------------------------- */
export interface BulkItem {
  repository_id: number;
  full_name: string | null;
  status: "pending" | "completed" | "failed" | "skipped" | "cancelled" | string;
  attempts: number;
  detail: string | null;
}

export interface BulkOperation {
  id: string;
  organization_id: number;
  type: BulkOperationType;
  parameters: Record<string, unknown>;
  status: BulkStatus;
  total: number;
  completed: number;
  failed: number;
  skipped: number;
  pending: number;
  requested_by: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancelled_by: string | null;
  items: BulkItem[];
  can_manage: boolean;
}

/* Scan schedules --------------------------------------------------------- */
export interface ScheduleRun {
  id: string;
  slot: string;
  state: "running" | "completed" | "partial" | "failed" | string;
  started_at: string;
  completed_at: string | null;
  repositories: number;
  queued: number;
  skipped: number;
  failed: number;
  /** Skip and failure reasons with counts. */
  detail: Record<string, number>;
}

export interface ScanSchedule {
  id: string;
  organization_id: number;
  name: string;
  target: PolicyTarget;
  cadence: "daily" | "weekly";
  hour: number;
  minute: number;
  /** 0 = Monday … 6 = Sunday (weekly only). */
  weekday: number | null;
  timezone: string;
  enabled: boolean;
  next_run_at: string | null;
  revision: number;
  repositories_covered: number;
  last_run: ScheduleRun | null;
  created_by: string | null;
  created_at: string;
  updated_by: string | null;
  updated_at: string;
  can_manage: boolean;
}

/* Organization rules ----------------------------------------------------- */
export interface OrganizationIdentity {
  id: string;
  display_name: string;
  names: string[];
  name_prefixes: string[];
  emails: string[];
  github_logins: string[];
}

export interface OrganizationRuleDocument {
  ai_identities: OrganizationIdentity[];
  bot_identities: OrganizationIdentity[];
}

export interface OrganizationRules {
  organization_id: number;
  version: number;
  fingerprint: string | null;
  document: OrganizationRuleDocument;
  /** As recorded with scans. */
  rules_version: string;
  created_at: string | null;
  created_by: string | null;
  reason: string | null;
  trust_levels: { level: string; source: string; can: string }[];
  can_manage: boolean;
}

export interface OrganizationRuleVersion {
  version: number;
  fingerprint: string;
  created_at: string;
  created_by: string | null;
  reason: string | null;
}
