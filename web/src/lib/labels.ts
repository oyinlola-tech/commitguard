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
  CircleHelp,
  CircleMinus,
  CircleSlash,
  CircleX,
  Clock,
  LoaderCircle,
  OctagonAlert,
  Plug,
  ShieldCheck,
  ShieldQuestion,
  ShieldX,
  TriangleAlert,
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
};

export const VIOLATION_STATUS: Record<string, StatusStyle> = {
  open: style("OPEN", "danger", OctagonAlert),
  acknowledged: style("ACKNOWLEDGED", "warning", CircleDashed),
  resolved: style("RESOLVED", "success", CircleCheck),
};

export const PROTECTION: Record<string, StatusStyle> = {
  protected: style("PROTECTED", "success", ShieldCheck),
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

export const RESULT_OPTIONS = ["pass", "warning", "blocked", "error", "running", "queued", "cancelled"] as const;
export const SEVERITY_OPTIONS = ["critical", "high", "medium", "low", "info"] as const;
export const RULE_OPTIONS = ["ai_coauthor", "ai_identity", "ai_trailer", "malformed_trailer", "bot_identity"] as const;
