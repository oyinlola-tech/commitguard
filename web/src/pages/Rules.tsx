import { useQuery } from "@tanstack/react-query";

import { listRules } from "../api/rules";
import type { Rule } from "../api/types";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { PageHeader, Panel } from "../components/Primitives";
import { QueryBoundary, EmptyState } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { POLICY_ACTION, SEVERITY } from "../lib/labels";
import { routes } from "../lib/routes";

export default function Rules() {
  useDocumentTitle("Rules");
  const query = useQuery({ queryKey: ["rules"], queryFn: listRules, staleTime: 300_000 });
  return (
    <>
      <PageHeader title="Rules" description="Detection rules bundled with the installed CommitGuard package. The same rule IDs appear in the CLI, Git hooks, GitHub Actions and the GitHub App." />
      <Panel flush>
        <QueryBoundary query={query} errorTitle="We could not load rules." isEmpty={(r) => r.length === 0} empty={<EmptyState title="No rules available." />}>
          {(rules) => (
            <DataTable<Rule>
              caption="Detection rules"
              rows={rules}
              rowKey={(r) => r.id}
              rowHref={(r) => routes.rule(r.id)}
              columns={[
                { key: "id", header: "Rule ID", primary: true, cell: (r) => <code>{r.id}</code> },
                { key: "name", header: "Name", cell: (r) => r.name },
                { key: "severity", header: "Severity", cell: (r) => <Badge map={SEVERITY} value={r.severity} compact /> },
                { key: "action", header: "Default action", cell: (r) => <Badge map={POLICY_ACTION} value={r.default_action} compact /> },
                { key: "source", header: "Source", cell: (r) => (r.trusted ? "Bundled · trusted" : "Bundled") },
                { key: "status", header: "Status", hideOnMobile: true, cell: (r) => (r.status === "active" ? "Active" : r.status) },
              ]}
            />
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
