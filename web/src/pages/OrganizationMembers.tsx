import { Link } from "react-router";

import { useSession } from "../auth/session";
import { Members } from "../components/Members";
import { OrganizationGate } from "../components/OrganizationGate";
import { PageHeader, Panel } from "../components/Primitives";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

export default function OrganizationMembers() {
  useDocumentTitle("Organization members");
  const { session } = useSession();
  return (
    <OrganizationGate title="Organization members" permission="members:read">
      {(scope) => (
        <>
          <PageHeader
            eyebrow={<Link to={routes.organizationSettings}>Organization settings</Link>}
            title="Members"
            description={`People with a CommitGuard role in ${scope.login}. Roles decide what they can see and change; every grant, change and removal is recorded in the audit log.`}
          />
          <Panel title={`Members · ${scope.login}`} id="members" flush>
            <Members key={scope.id} access={scope.access} userId={session.user.id} />
          </Panel>
        </>
      )}
    </OrganizationGate>
  );
}
