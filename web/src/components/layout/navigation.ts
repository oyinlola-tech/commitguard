import type { LucideIcon } from "lucide-react";
import { Blocks, FolderGit2, LayoutDashboard, OctagonAlert, Plug, Scale, ScanLine, ScrollText, Settings } from "lucide-react";

import type { Permission } from "../../api/types";
import { routes } from "../../lib/routes";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  permission?: Permission;
  group?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: routes.overview, label: "Overview", icon: LayoutDashboard, permission: "repositories:read" },
  { to: routes.repositories, label: "Repositories", icon: FolderGit2, permission: "repositories:read" },
  { to: routes.scans, label: "Scans", icon: ScanLine, permission: "scans:read" },
  { to: routes.violations, label: "Violations", icon: OctagonAlert, permission: "violations:read" },
  { to: routes.policies, label: "Policies", icon: Scale, permission: "policies:read" },
  { to: routes.rules, label: "Rules", icon: Blocks, permission: "rules:read" },
  { to: routes.audit, label: "Audit log", icon: ScrollText, permission: "audit:read" },
  { to: routes.installations, label: "Installations", icon: Plug, permission: "repositories:read", group: "GitHub" },
  { to: routes.settings, label: "Settings", icon: Settings },
];
