/**
 * The dashboard's status vocabulary: one label, one tone and one icon per
 * server value. Every status is shown as text (never colour alone), and the
 * same word is used for the same state everywhere.
 */

import type { LucideIcon } from "lucide-react";
import {
  Ban,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  CircleMinus,
  CircleSlash,
  CircleX,
  Clock,
  Eye,
  FilePen,
  FlaskConical,
  GitCompare,
  LoaderCircle,
  Lock,
  OctagonAlert,
  Pause,
  Plug,
  ShieldAlert,
  ShieldCheck,
  ShieldMinus,
  ShieldQuestion,
  ShieldX,
  SlidersHorizontal,
  TriangleAlert,
  Undo2,
  Unplug,
} from "lucide-react";

export type Tone = "success" | "danger" | "warning" | "info" | "neutral" | "critical";

export interface StatusStyle {
  label: string;
  tone: Tone;
  icon: LucideIcon;
}

const style = (label: string, tone: Tone, icon: LucideIcon): StatusStyle => ({ label, tone, icon });

export const SCAN_RESULT: Record<string, StatusStyle> = {
  pass: style("PASS", "success", CircleCheck),
  warning: style("WARNING", "warning", TriangleAlert),
  blocked: style("BLOCKED", "danger", Ban),
  error: style("ERROR", "danger", CircleX),
  running: style("RUNNING", "info", LoaderCircle),
  queued: style("QUEUED", "neutral", Clock),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
  stale: style("STALE", "neutral", Clock),
};

export const VIOLATION_STATUS: Record<string, StatusStyle> = {
  open: style("OPEN", "danger", OctagonAlert),
  acknowledged: style("ACKNOWLEDGED", "warning", CircleDashed),
  resolved: style("RESOLVED", "success", CircleCheck),
};

export const PROTECTION: Record<string, StatusStyle> = {
  protected: style("PROTECTED", "success", ShieldCheck),
  at_risk: style("AT RISK", "critical", ShieldAlert),
  unprotected: style("UNPROTECTED", "danger", ShieldX),
  configuration_error: style("CONFIGURATION ERROR", "danger", TriangleAlert),
  unknown: style("UNKNOWN", "neutral", ShieldQuestion),
};

export const APP_CONNECTION: Record<string, StatusStyle> = {
  connected: style("CONNECTED", "success", Plug),
  suspended: style("SUSPENDED", "warning", TriangleAlert),
  disconnected: style("DISCONNECTED", "danger", Unplug),
};

export const INTEGRATION: Record<string, StatusStyle> = {
  connected: style("CONNECTED", "success", Plug),
  action_required: style("ACTION REQUIRED", "warning", TriangleAlert),
  disconnected: style("DISCONNECTED", "danger", Unplug),
};

export const ACTIONS_STATUS: Record<string, StatusStyle> = {
  detected: style("DETECTED", "success", CircleCheck),
  not_detected: style("NOT DETECTED", "neutral", CircleSlash),
  unknown: style("UNKNOWN", "neutral", CircleHelp),
};

export const REQUIRED_CHECK: Record<string, StatusStyle> = {
  required: style("REQUIRED", "success", ShieldCheck),
  not_required: style("NOT REQUIRED", "danger", ShieldX),
  unknown: style("UNKNOWN", "neutral", ShieldQuestion),
};

export const LOCAL_HOOKS: Record<string, StatusStyle> = {
  not_verifiable: style("NOT VERIFIABLE", "neutral", CircleHelp),
};

export const MERGE_QUEUE: Record<string, StatusStyle> = {
  enabled: style("ENABLED", "success", CircleCheck),
  not_enabled: style("NOT ENABLED", "neutral", CircleSlash),
  unknown: style("UNKNOWN", "neutral", CircleHelp),
};

export const MERGE_GROUP_STATE: Record<string, StatusStyle> = {
  checks_requested: style("IN QUEUE", "info", LoaderCircle),
  destroyed: style("ENDED", "neutral", CircleSlash),
};

export const NOTIFICATION_STATE: Record<string, StatusStyle> = {
  unread: style("UNREAD", "info", CircleDashed),
  read: style("READ", "neutral", CircleCheck),
  archived: style("ARCHIVED", "neutral", CircleSlash),
};

export const DELIVERY_STATUS: Record<string, StatusStyle> = {
  pending: style("PENDING", "warning", Clock),
  sent: style("SENT", "success", CircleCheck),
  failed: style("FAILED", "danger", CircleX),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
};

export const POLICY_VERSION_STATUS: Record<string, StatusStyle> = {
  active: style("ACTIVE", "success", CircleCheck),
  archived: style("ARCHIVED", "neutral", CircleSlash),
};

export const TRIGGER_LABEL: Record<string, string> = {
  push: "Push",
  pull_request: "Pull request",
  merge_group: "Merge queue",
  manual: "Manual",
  rerun: "Re-run",
  retry: "Automatic retry",
};

export const HEALTH: Record<string, StatusStyle> = {
  ok: style("OK", "success", CircleCheck),
  attention: style("ATTENTION", "warning", TriangleAlert),
  unknown: style("UNKNOWN", "neutral", CircleHelp),
};

export const SEVERITY: Record<string, StatusStyle> = {
  critical: style("CRITICAL", "critical", OctagonAlert),
  high: style("HIGH", "danger", TriangleAlert),
  medium: style("MEDIUM", "warning", TriangleAlert),
  low: style("LOW", "neutral", CircleMinus),
  info: style("INFO", "neutral", CircleHelp),
};

export const POLICY_ACTION: Record<string, StatusStyle> = {
  block: style("BLOCK", "danger", Ban),
  warn: style("WARN", "warning", TriangleAlert),
  allow: style("ALLOW", "neutral", CircleCheck),
};

export const ROLE_LABEL: Record<string, string> = {
  viewer: "Viewer",
  security_manager: "Security manager",
  admin: "Admin",
  owner: "Owner",
};

const UNKNOWN_STYLE = style("UNKNOWN", "neutral", CircleHelp);

export function statusStyle(map: Record<string, StatusStyle>, value: string | null | undefined): StatusStyle {
  return (value && map[value]) || UNKNOWN_STYLE;
}

export const RESULT_OPTIONS = ["pass", "warning", "blocked", "error", "running", "queued", "cancelled", "stale"] as const;
export const SEVERITY_OPTIONS = ["critical", "high", "medium", "low", "info"] as const;
export const RULE_OPTIONS = ["ai_coauthor", "ai_identity", "ai_trailer", "malformed_trailer", "bot_identity"] as const;

/* ------------------------------------------------------------------------ */
/* Organization governance                                                  */
/* ------------------------------------------------------------------------ */

/** Posture is a state with explicit reasons, never a score. */
export const POSTURE: Record<string, StatusStyle> = {
  secure: style("SECURE", "success", ShieldCheck),
  at_risk: style("AT RISK", "critical", ShieldAlert),
  unprotected: style("UNPROTECTED", "danger", ShieldX),
  unknown: style("UNKNOWN", "neutral", ShieldQuestion),
};

export const SYNC_HEALTH: Record<string, StatusStyle> = {
  healthy: style("HEALTHY", "success", CircleCheck),
  syncing: style("SYNCING", "info", LoaderCircle),
  degraded: style("DEGRADED", "warning", TriangleAlert),
  failed: style("FAILED", "danger", CircleX),
  never: style("NEVER SYNCHRONISED", "neutral", CircleDashed),
};

export const INSTALLATION_STATE: Record<string, StatusStyle> = {
  active: style("ACTIVE", "success", Plug),
  suspended: style("SUSPENDED", "warning", TriangleAlert),
  deleted: style("REMOVED", "danger", Unplug),
};

export const REPOSITORY_MODE: Record<string, StatusStyle> = {
  enforce: style("ENFORCE", "neutral", Lock),
  monitor: style("MONITOR", "warning", Eye),
};

export const ONBOARDING: Record<string, StatusStyle> = {
  onboarded: style("ONBOARDED", "neutral", CircleCheck),
  discovered: style("DISCOVERED", "info", CircleDashed),
  excluded: style("EXCLUDED", "neutral", CircleSlash),
};

/** Effective policy propagation. Only `up_to_date` means the current policy applies. */
export const PROPAGATION: Record<string, StatusStyle> = {
  up_to_date: style("UP TO DATE", "success", CircleCheck),
  stale: style("STALE", "warning", Clock),
  syncing: style("SYNCING", "info", LoaderCircle),
  error: style("ERROR", "danger", CircleX),
  pending: style("PENDING", "neutral", CircleDashed),
};

export const DRIFT: Record<string, StatusStyle> = {
  compliant: style("COMPLIANT", "success", CircleCheck),
  customized: style("CUSTOMIZED", "neutral", SlidersHorizontal),
  drift: style("DRIFT", "warning", GitCompare),
  unknown: style("UNKNOWN", "neutral", CircleHelp),
};

export const DRAFT_STATE: Record<string, StatusStyle> = {
  draft: style("DRAFT", "neutral", FilePen),
  pending_approval: style("PENDING APPROVAL", "warning", Clock),
  approved: style("APPROVED", "info", CircleCheck),
  rejected: style("REJECTED", "danger", CircleX),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
  published: style("PUBLISHED", "success", CircleCheck),
};

export const APPROVAL_STATUS: Record<string, StatusStyle> = {
  pending: style("PENDING", "warning", Clock),
  approved: style("APPROVED", "success", CircleCheck),
  rejected: style("REJECTED", "danger", CircleX),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
};

export const EXCEPTION_STATUS: Record<string, StatusStyle> = {
  requested: style("REQUESTED", "info", Clock),
  active: style("ACTIVE", "warning", ShieldMinus),
  expired: style("EXPIRED", "neutral", Clock),
  revoked: style("REVOKED", "neutral", CircleSlash),
  rejected: style("REJECTED", "neutral", CircleX),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
};

export const ROLLOUT_STATE: Record<string, StatusStyle> = {
  pilot: style("PILOT", "info", FlaskConical),
  rollout: style("ROLLING OUT", "info", CircleDot),
  active: style("ALL STAGES ENROLLED", "success", CircleCheck),
  paused: style("PAUSED", "warning", Pause),
  rolled_back: style("ROLLED BACK", "danger", Undo2),
};

export const STAGE_STATE: Record<string, StatusStyle> = {
  done: style("DONE", "success", CircleCheck),
  current: style("CURRENT", "info", CircleDot),
  planned: style("PLANNED", "neutral", CircleDashed),
};

export const SIMULATION_STATE: Record<string, StatusStyle> = {
  queued: style("QUEUED", "neutral", Clock),
  running: style("RUNNING", "info", LoaderCircle),
  completed: style("COMPLETED", "success", CircleCheck),
  failed: style("FAILED", "danger", CircleX),
};

export const BULK_STATUS: Record<string, StatusStyle> = {
  queued: style("QUEUED", "neutral", Clock),
  running: style("RUNNING", "info", LoaderCircle),
  completed: style("COMPLETED", "success", CircleCheck),
  partial: style("PARTIAL", "warning", TriangleAlert),
  failed: style("FAILED", "danger", CircleX),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
};

export const BULK_ITEM_STATUS: Record<string, StatusStyle> = {
  pending: style("PENDING", "neutral", Clock),
  completed: style("COMPLETED", "success", CircleCheck),
  failed: style("FAILED", "danger", CircleX),
  skipped: style("SKIPPED", "neutral", CircleMinus),
  cancelled: style("CANCELLED", "neutral", CircleSlash),
};

export const SCHEDULE_RUN_STATE: Record<string, StatusStyle> = {
  running: style("RUNNING", "info", LoaderCircle),
  completed: style("COMPLETED", "success", CircleCheck),
  partial: style("PARTIAL", "warning", TriangleAlert),
  failed: style("FAILED", "danger", CircleX),
};

export const ENABLED_STATE: Record<string, StatusStyle> = {
  enabled: style("ENABLED", "success", CircleCheck),
  disabled: style("DISABLED", "neutral", CircleSlash),
};

export const STRENGTH_LABEL: Record<string, string> = {
  mandatory: "Mandatory",
  default: "Default",
};

/** Where an effective rule came from (`commitguard.policies.governance.PolicyLevel`). */
export const POLICY_LEVEL_LABEL: Record<string, string> = {
  built_in: "Built-in default",
  service: "Service policy",
  organization: "Organization",
  group: "Repository group",
  repository_policy: "Repository policy",
  repository_configuration: "Repository configuration",
  exception: "Exception",
  monitor_mode: "Monitor mode",
};

export const TARGET_TYPE_LABEL: Record<string, string> = {
  organization: "Organization",
  group: "Group",
  repository: "Repository",
};

export const BULK_TYPE_LABEL: Record<string, string> = {
  add_to_group: "Add to group",
  remove_from_group: "Remove from group",
  onboard: "Onboard",
  set_mode: "Change mode",
  set_monitoring: "Pause or resume monitoring",
  schedule_scan: "Scan default branch",
};

export const REPORT_LABEL: Record<string, { label: string; description: string }> = {
  compliance: { label: "Compliance", description: "Posture, reasons, mode, policy state and drift of every repository." },
  coverage: { label: "Coverage", description: "Which repositories are connected, onboarded, enforcing and scanned." },
  violations: { label: "Violations", description: "Violations grouped by rule, severity, action, status and repository." },
  exceptions: { label: "Exceptions", description: "Every exception with scope, status, requester, approver and expiry." },
  policy_changes: { label: "Policy changes", description: "Policy publications, rollbacks, approvals and settings changes from the audit log." },
  installations: { label: "GitHub installations", description: "Installation state and synchronisation health." },
};

export const POSTURE_OPTIONS = ["at_risk", "unprotected", "unknown", "secure"] as const;
export const PROPAGATION_OPTIONS = ["up_to_date", "stale", "syncing", "error", "pending"] as const;
export const EXCEPTION_STATUS_OPTIONS = ["requested", "active", "expired", "revoked", "rejected", "cancelled"] as const;

/** Audit event types added by organization governance (`commitguard.audit.models`). */
export const GOVERNANCE_AUDIT_TYPES = [
  "organization_settings_changed", "repository_discovered", "repository_onboarded", "repository_excluded", "repository_mode_changed",
  "repository_archived", "repository_sync_failed", "repository_group_created", "repository_group_updated", "repository_group_archived",
  "repository_group_members_added", "repository_group_members_removed", "policy_draft_created", "policy_draft_updated",
  "policy_draft_cancelled", "policy_approval_requested", "policy_approved", "policy_rejected", "policy_published",
  "policy_emergency_published", "policy_rolled_back", "policy_simulated", "policy_rollout_started", "policy_rollout_advanced",
  "policy_rollout_paused", "policy_rollout_resumed", "policy_rollout_completed", "policy_rollout_rolled_back",
  "policy_propagation_failed", "exception_requested", "exception_approved", "exception_rejected", "exception_cancelled",
  "exception_revoked", "exception_expired", "organization_rules_changed", "scan_schedule_created", "scan_schedule_changed",
  "scan_schedule_disabled", "scheduled_scans_queued", "bulk_operation_requested", "bulk_operation_finished",
  "bulk_operation_cancelled", "security_event_acknowledged", "report_exported",
] as const;
