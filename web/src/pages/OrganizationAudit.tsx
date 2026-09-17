import { Lock } from "lucide-react";
import { Link } from "react-router";

import { AuditLogView } from "../components/AuditLogView";
import { OrganizationGate } from "../components/OrganizationGate";
import { PageHeader } from "../components/Primitives";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

export default function OrganizationAudit() {
  useDocumentTitle("Organization audit");
  return (
    <OrganizationGate title="Organization audit" permission="audit:read">
      {(scope) => (
        <>
          <PageHeader
            eyebrow={<Link to={routes.organization}>{scope.login}</Link>}
            title="Organization audit"
            description={
              <>
                <Lock size={12} aria-hidden="true" /> Governance and security actions in {scope.login}: settings, groups, policy drafts, approvals, publications, rollouts, exceptions, rules, schedules, bulk operations and exports. Events cannot be edited or deleted.
              </>
            }
          />
          <AuditLogView key={scope.id} organization={scope.id} governance />
        </>
      )}
    </OrganizationGate>
  );
}
