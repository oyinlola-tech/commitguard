import { useCallback } from "react";

import type { OrganizationAccess, Permission } from "../api/types";
import { useSession } from "../auth/session";

export interface GovernanceScope {
  access: OrganizationAccess;
  id: number;
  login: string;
  /** Whether the session holds `permission` in this organization (display only; the server decides). */
  has: (permission: Permission) => boolean;
}

/**
 * Organization pages work on one organization: the one chosen in the top bar,
 * or the first one the user belongs to when "All organizations" is selected
 * (`implicit`, so the page can say which one it shows).
 */
export function useGovernanceOrganization(): { scope: GovernanceScope | null; implicit: boolean } {
  const { organizations, organization } = useSession();
  const access = organizations.find((o) => o.organization.id === organization) ?? organizations[0] ?? null;
  const has = useCallback((permission: Permission) => Boolean(access?.permissions.includes(permission)), [access]);
  if (!access) return { scope: null, implicit: false };
  return {
    scope: { access, id: access.organization.id, login: access.organization.login, has },
    implicit: organization === null && organizations.length > 1,
  };
}
