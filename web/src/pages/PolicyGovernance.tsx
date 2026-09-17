import { useQuery } from "@tanstack/react-query";
import { FilePen, Plus } from "lucide-react";
import { Link } from "react-router";

import { getSecurityPolicies } from "../api/governance";
import type { PolicyDraft, Rollout, SecurityPolicies } from "../api/types";
import { Badge } from "../components/Badge";
import { OrganizationGate } from "../components/OrganizationGate";
import { PageHeader, Panel, Time } from "../components/Primitives";
import { Propagation, RolloutProgress } from "../components/Rollouts";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { count } from "../lib/format";
import { APPROVAL_STATUS, DRAFT_STATE, ROLLOUT_STATE, TARGET_TYPE_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";

const DRAFT_GROUPS: { state: PolicyDraft["state"]; title: string; empty: string }[] = [
  { state: "pending_approval", title: "Waiting for approval", empty: "No policy change is waiting for approval." },
  { state: "approved", title: "Approved, ready to publish", empty: "No approved change is waiting to be published." },
  { state: "draft", title: "Drafts", empty: "No drafts." },
  { state: "rejected", title: "Rejected", empty: "No rejected drafts." },
  { state: "published", title: "Published", empty: "Nothing published through the workflow yet." },
  { state: "cancelled", title: "Cancelled", empty: "No cancelled drafts." },
];

interface TargetRow {
  key: string;
  type: "organization" | "group" | "repository";
  id: string;
  label: string;
  version: number;
  updatedAt: string | null;
  updatedBy: string | null;
  link: string;
}

function targetRows(data: SecurityPolicies, organizationId: number): TargetRow[] {
  const organization = data.targets.organization;
  return [
    {
      key: "organization",
      type: "organization",
      id: "",
      label: organization.organization.login,
      version: organization.version,
      updatedAt: organization.updated_at,
      updatedBy: organization.updated_by?.login ?? null,
      link: routes.policy(organizationId),
    },
    ...[...data.targets.groups, ...data.targets.repositories].map((policy) => ({
      key: `${policy.target.type}-${policy.target.id}`,
      type: policy.target.type,
      id: policy.target.id,
      label: policy.target.label,
      version: policy.version,
      updatedAt: policy.updated_at,
      updatedBy: policy.updated_by?.login ?? null,
      link: policy.target.type === "group" ? routes.group(policy.target.id) : routes.repository(Number(policy.target.id)),
    })),
  ];
}

function Targets({ data, scope }: { data: SecurityPolicies; scope: GovernanceScope }) {
  const rows = targetRows(data, scope.id);
  const canWrite = scope.has("policies:write");
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">Policy targets</caption>
        <thead>
          <tr><th scope="col">Policy</th><th scope="col">Scope</th><th scope="col">Version</th><th scope="col">Updated</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td data-label="Policy" className="table__primary">
                <Link to={row.link} className="strong break">{row.label}</Link>
              </td>
              <td data-label="Scope">{TARGET_TYPE_LABEL[row.type]}</td>
              <td data-label="Version">{row.version ? `v${row.version}` : <span className="muted">Not published</span>}</td>
              <td data-label="Updated" className="small">{row.updatedAt ? <>{row.updatedBy ?? "unknown"} · <Time value={row.updatedAt} /></> : <span className="muted">—</span>}</td>
              <td data-label="Actions" className="align-end">
                {canWrite ? (
                  <Link to={routes.newDraft({ type: row.type, id: row.id })} className="button button--ghost">
                    <FilePen size={14} aria-hidden="true" /> Propose change<span className="visually-hidden"> to {row.label}</span>
                  </Link>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="panel__inset muted small">A repository policy appears here once one has been published. To propose the first policy for a repository, choose it as the target of a new policy change.</p>
    </div>
  );
}

function approvalSummary(draft: PolicyDraft) {
  const latest = draft.approvals[0];
  if (!latest) return <span className="muted">{draft.requires_approval ? "Approval required" : "No approval requested"}</span>;
  return (
    <span className="stack">
      <Badge map={APPROVAL_STATUS} value={latest.status} compact />
      <span className="muted small">{latest.decided_by ? `by ${latest.decided_by}` : `requested by ${latest.requested_by ?? "unknown"}`}</span>
    </span>
  );
}

function Drafts({ drafts }: { drafts: PolicyDraft[] }) {
  return (
    <div className="stack-lg">
      {DRAFT_GROUPS.map((group) => {
        const items = drafts.filter((d) => d.state === group.state);
        const table = items.length ? (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">{group.title}</caption>
              <thead>
                <tr><th scope="col">Change</th><th scope="col">Target</th><th scope="col">State</th><th scope="col">Approval</th><th scope="col">Author</th><th scope="col">Updated</th></tr>
              </thead>
              <tbody>
                {items.map((d) => (
                  <tr key={d.id} className="table__row--link">
                    <td data-label="Change" className="table__primary">
                      <Link to={routes.draft(d.id)} className="row-link">
                        <span className="strong break">{d.title}</span>
                      </Link>
                      {d.weakening ? <span className="tag tag--danger">Weakens enforcement</span> : null}
                      {d.rebase_required ? <span className="tag tag--warning">Target changed</span> : null}
                      {d.emergency ? <span className="tag tag--danger">Emergency</span> : null}
                    </td>
                    <td data-label="Target">{d.target.label}</td>
                    <td data-label="State">
                      <Badge map={DRAFT_STATE} value={d.state} compact />
                      {d.published_version ? <span className="muted small"> v{d.published_version}</span> : null}
                    </td>
                    <td data-label="Approval">{approvalSummary(d)}</td>
                    <td data-label="Author">{d.created_by ?? "unknown"}</td>
                    <td data-label="Updated"><Time value={d.updated_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted small">{group.empty}</p>
        );
        const heading = (
          <>
            {group.title} <span className="muted">({count(items.length)})</span>
          </>
        );
        return group.state === "cancelled" || group.state === "published" ? (
          <details key={group.state} className="disclosure drafts-group">
            <summary>{heading}</summary>
            {table}
          </details>
        ) : (
          <section key={group.state} className="drafts-group" aria-labelledby={`drafts-${group.state}`}>
            <h3 className="subheading" id={`drafts-${group.state}`}>{heading}</h3>
            {table}
          </section>
        );
      })}
    </div>
  );
}

function Rollouts({ rollouts }: { rollouts: Rollout[] }) {
  if (rollouts.length === 0) return <EmptyState title="No staged rollouts.">Publish an organization or group policy with stages to roll it out to pilot repositories first.</EmptyState>;
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">Staged rollouts</caption>
        <thead>
          <tr><th scope="col">Rollout</th><th scope="col">State</th><th scope="col">Stage</th><th scope="col">Progress</th><th scope="col">Started</th></tr>
        </thead>
        <tbody>
          {rollouts.map((r) => (
            <tr key={r.id} className="table__row--link">
              <td data-label="Rollout" className="table__primary">
                <Link to={routes.rollout(r.id)} className="row-link">
                  <span className="stack"><span className="strong break">{r.target.label}</span><span className="muted small">v{r.from_version} → v{r.to_version}</span></span>
                </Link>
              </td>
              <td data-label="State">
                <Badge map={ROLLOUT_STATE} value={r.state} compact />
                {r.complete ? <span className="tag">Complete</span> : null}
              </td>
              <td data-label="Stage">{r.stages[r.current_stage]?.name ?? "—"} <span className="muted small">({count(r.current_stage + 1)} of {count(r.stages.length)})</span></td>
              <td data-label="Progress"><RolloutProgress rollout={r} /></td>
              <td data-label="Started" className="small">{r.created_by ?? "unknown"} · <Time value={r.created_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Governance({ scope }: { scope: GovernanceScope }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "policies"], queryFn: () => getSecurityPolicies(scope.id), refetchInterval: 60_000 });
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organization}>{scope.login}</Link>}
        title="Policy governance"
        description="Organization, group and repository policies, the changes proposed to them, staged rollouts and whether every repository resolves the current policy."
        actions={
          scope.has("policies:write") ? (
            <Link className="button button--primary" to={routes.newDraft()}>
              <Plus size={14} aria-hidden="true" /> New policy change
            </Link>
          ) : null
        }
      />
      <QueryBoundary query={query} errorTitle="We could not load policy governance." loading={<SkeletonRows rows={8} label="Loading policies…" />}>
        {(data) => {
          const inProgress = data.rollouts.filter((r) => ["pilot", "rollout", "paused"].includes(r.state));
          const finished = data.rollouts.filter((r) => !["pilot", "rollout", "paused"].includes(r.state));
          return (
            <div className="stack-lg">
              <Panel title="Policies" id="targets" flush>
                <Targets data={data} scope={scope} />
              </Panel>
              <Panel title="Policy changes" id="drafts">
                <Drafts drafts={data.drafts} />
              </Panel>
              <div className="stack-lg">
                <Panel title="Rollouts in progress" id="rollouts" flush>
                  <Rollouts rollouts={inProgress} />
                  {finished.length ? (
                    <details className="disclosure panel__inset">
                      <summary>Finished rollouts ({count(finished.length)})</summary>
                      <Rollouts rollouts={finished} />
                    </details>
                  ) : null}
                </Panel>
                <Panel title="Propagation" id="propagation">
                  <Propagation propagation={data.propagation} />
                </Panel>
              </div>
            </div>
          );
        }}
      </QueryBoundary>
    </>
  );
}

export default function PolicyGovernance() {
  useDocumentTitle("Policy governance");
  return (
    <OrganizationGate title="Policy governance" permission="policies:read">
      {(scope) => <Governance key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
