import type { LucideIcon } from "lucide-react";
import {
  Bell,
  Blocks,
  Building2,
  ChartColumn,
  FileClock,
  FolderGit2,
  LayoutDashboard,
  OctagonAlert,
  Plug,
  Scale,
  ScanLine,
  ScrollText,
  Settings,
  ShieldMinus,
  SlidersHorizontal,
  Table2,
  Workflow,
} from "lucide-react";

import type { Permission } from "../../api/types";
import { routes } from "../../lib/routes";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  permission?: Permission;
  group?: string;
  /** Active only on this exact path (not on the pages below it). */
  end?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: routes.overview, label: "Overview", icon: LayoutDashboard, permission: "repositories:read", end: true },
  { to: routes.repositories, label: "Repositories", icon: FolderGit2, permission: "repositories:read" },
  { to: routes.scans, label: "Scans", icon: ScanLine, permission: "scans:read" },
  { to: routes.violations, label: "Violations", icon: OctagonAlert, permission: "violations:read" },
  { to: routes.policies, label: "Policies", icon: Scale, permission: "policies:read" },
  { to: routes.rules, label: "Rules", icon: Blocks, permission: "rules:read" },
  { to: routes.notifications, label: "Notifications", icon: Bell },
  { to: routes.audit, label: "Audit log", icon: ScrollText, permission: "audit:read" },
  { to: routes.organization, label: "Command center", icon: Building2, permission: "security:read", group: "Organization", end: true },
  { to: routes.organizationRepositories, label: "Repository matrix", icon: Table2, permission: "security:read", group: "Organization" },
  { to: routes.policyGovernance, label: "Policy governance", icon: Workflow, permission: "policies:read", group: "Organization" },
  { to: routes.exceptions, label: "Exceptions", icon: ShieldMinus, permission: "exceptions:read", group: "Organization" },
  { to: routes.organizationSecurity, label: "Trends and reports", icon: ChartColumn, permission: "security:read", group: "Organization" },
  { to: routes.organizationAudit, label: "Organization audit", icon: FileClock, permission: "audit:read", group: "Organization" },
  { to: routes.organizationSettings, label: "Organization settings", icon: SlidersHorizontal, permission: "organization:read", group: "Organization" },
  { to: routes.installations, label: "Installations", icon: Plug, permission: "repositories:read", group: "GitHub" },
  { to: routes.settings, label: "Settings", icon: Settings, end: true },
];
