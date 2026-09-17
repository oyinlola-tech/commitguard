import type { ReactNode } from "react";

import type { Permission } from "../api/types";
import { useGovernanceOrganization, type GovernanceScope } from "../hooks/useGovernanceOrganization";
import { Notice, PageHeader } from "./Primitives";
import { AccessDenied, EmptyState } from "./States";

/**
 * Renders an organization page for the selected organization, or explains why
 * it cannot: no organization, or a role without `permission`. Hiding a page is
 * a convenience - the API refuses the same requests.
 */
export function OrganizationGate({
  title,
  permission,
  children,
}: {
  title: string;
  permission?: Permission;
  children: (scope: GovernanceScope) => ReactNode;
}) {
  const { scope, implicit } = useGovernanceOrganization();
  if (!scope) {
    return (
      <>
        <PageHeader title={title} />
        <EmptyState title="You do not have access to an organization yet.">
          Ask an owner of your organization to grant you a role in CommitGuard.
        </EmptyState>
      </>
    );
  }
  if (permission && !scope.has(permission)) {
    return (
      <>
        <PageHeader title={title} eyebrow={scope.login} />
        <AccessDenied />
      </>
    );
  }
  return (
    <>
      {implicit ? (
        <Notice>
          Showing <strong>{scope.login}</strong>. Choose another organization in the top bar to switch.
        </Notice>
      ) : null}
      {children(scope)}
    </>
  );
}
