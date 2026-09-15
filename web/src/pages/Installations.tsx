import { useQuery } from "@tanstack/react-query";

import { listInstallations } from "../api/github";
import type { Installation } from "../api/types";
import { useSession } from "../auth/session";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { count } from "../lib/format";
import { APP_CONNECTION } from "../lib/labels";
import { routes } from "../lib/routes";

export default function Installations() {
  useDocumentTitle("GitHub installations");
  const { organization } = useSession();
  const query = useQuery({ queryKey: ["installations", organization], queryFn: () => listInstallations(organization) });
  return (
    <>
      <PageHeader eyebrow="GitHub" title="Installations" description="GitHub accounts where the CommitGuard App is installed and that you can access." />
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load installations."
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState title="No GitHub App installations yet.">Install the CommitGuard GitHub App on an organization or account, then sign in again.</EmptyState>}
        >
          {(installations) => (
            <DataTable<Installation>
              caption="GitHub App installations"
              rows={installations}
              rowKey={(i) => i.id}
              rowHref={(i) => routes.installation(i.id)}
              columns={[
                { key: "account", header: "Account", primary: true, cell: (i) => <span className="strong">{i.account.login}</span> },
                { key: "type", header: "Type", cell: (i) => i.account.type },
                { key: "id", header: "Installation ID", hideOnMobile: true, cell: (i) => <code>{i.id}</code> },
                { key: "repositories", header: "Repositories", align: "end", cell: (i) => count(i.repositories) },
                { key: "status", header: "Status", cell: (i) => <Badge map={APP_CONNECTION} value={i.status} compact /> },
                { key: "installed", header: "Installed", hideOnMobile: true, cell: (i) => <Time value={i.installed_at} /> },
                { key: "event", header: "Last event", cell: (i) => <Time value={i.last_event_at} /> },
              ]}
            />
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
