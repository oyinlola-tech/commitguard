import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { getRule } from "../api/rules";
import { Badge } from "../components/Badge";
import { KeyValueList, PageHeader, Panel } from "../components/Primitives";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { POLICY_ACTION, SEVERITY } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

export default function RuleDetail() {
  const { ruleId = "" } = useParams();
  const valid = /^[a-z][a-z0-9_]{0,63}$/.test(ruleId);
  const query = useQuery({ queryKey: ["rule", ruleId], queryFn: () => getRule(ruleId), enabled: valid, staleTime: 300_000 });
  useDocumentTitle(query.data?.rule.name ?? "Rule");
  if (!valid) return <NotFoundContent resource="rule" />;
  if (query.isPending) return <SkeletonRows rows={6} label="Loading rule…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="rule" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this rule." error={query.error} onRetry={() => void query.refetch()} />;
  const { rule, remediation, evidence_sources, data_files } = query.data;
  return (
    <>
      <PageHeader eyebrow={<Link to={routes.rules}>Rules</Link>} title={rule.name} description={rule.description}>
        <div className="badge-row">
          <code className="rule-id">{rule.id}</code>
          <Badge map={SEVERITY} value={rule.severity} />
          <Badge map={POLICY_ACTION} value={rule.default_action} />
        </div>
      </PageHeader>
      <div className="grid-2">
        <Panel title="Rule" id="rule">
          <KeyValueList
            items={[
              ["Rule ID", <code>{rule.id}</code>],
              ["Detector", <code>{rule.detector}</code>],
              ["Severity", <Badge map={SEVERITY} value={rule.severity} compact />],
              ["Default policy action", <Badge map={POLICY_ACTION} value={rule.default_action} compact />],
              ["Source", rule.trusted ? "Bundled with CommitGuard · trusted" : "Bundled"],
              ["Rules version", <code>{rule.rules_version}</code>],
              ["CommitGuard version", rule.tool_version],
              ["Evidence", evidence_sources.join(", ")],
            ]}
          />
          <p className="muted small">
            <Lock size={12} aria-hidden="true" /> Bundled rules ship with the installed package and cannot be edited here. The action a rule triggers is set by <Link to={routes.policies}>policy</Link>.
          </p>
        </Panel>
        <Panel title="Rule data" id="data">
          <ul className="plain-list">
            {data_files.map((file) => (
              <li key={file.name}><code>rules/{file.name}</code> · {file.entries} entries</li>
            ))}
          </ul>
        </Panel>
      </div>
      <Panel title="Remediation" id="remediation">
        <ol className="steps">
          {remediation.map((step, index) => <li key={index}>{step}</li>)}
        </ol>
      </Panel>
    </>
  );
}
