import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { Link } from "react-router";

import { listExceptions } from "../api/exceptions";
import { getSecurityExceptions, listRepositoryMatrix } from "../api/governance";
import { listGroups } from "../api/groups";
import type { ExceptionStatus, PolicyException } from "../api/types";
import { Badge } from "../components/Badge";
import { FilterBar, SelectField } from "../components/FilterBar";
import { OrganizationGate } from "../components/OrganizationGate";
import { Pagination, useCursorPager } from "../components/Pagination";
import { Metric, PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { useUrlState } from "../hooks/useUrlState";
import { count } from "../lib/format";
import { EXCEPTION_STATUS, EXCEPTION_STATUS_OPTIONS, POLICY_ACTION, RULE_OPTIONS, TARGET_TYPE_LABEL, statusStyle } from "../lib/labels";
import { routes } from "../lib/routes";

const KEYS = ["status", "rule", "repository", "group"] as const;

export function ExpiryText({ exception }: { exception: PolicyException }) {
  if (exception.permanent) return <span className="strong">Permanent</span>;
  return (
    <span className="stack">
      <Time value={exception.expires_at} />
      {exception.expiring_soon && exception.status === "active" ? <span className="tag tag--warning">Expiring soon</span> : null}
    </span>
  );
}

function ExceptionsTable({ items }: { items: PolicyException[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">Policy exceptions</caption>
        <thead>
          <tr><th scope="col">Rule</th><th scope="col">Scope</th><th scope="col">Lowered to</th><th scope="col">Status</th><th scope="col">Expires</th><th scope="col">Requested</th></tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id} className="table__row--link">
              <td data-label="Rule" className="table__primary">
                <Link to={routes.exception(e.id)} className="row-link">
                  <span className="stack">
                    <span className="strong">{e.rule_name}</span>
                    <code className="muted">{e.rule_id}</code>
                  </span>
                </Link>
              </td>
              <td data-label="Scope">
                <span className="stack">
                  <span className="break">{e.scope.label}</span>
                  <span className="muted small">{TARGET_TYPE_LABEL[e.scope.type]}</span>
                </span>
              </td>
              <td data-label="Lowered to"><Badge map={POLICY_ACTION} value={e.action} compact /></td>
              <td data-label="Status">
                <span className="stack">
                  <Badge map={EXCEPTION_STATUS} value={e.status} compact />
                  {e.status === "requested" && e.requires_approval ? <span className="muted small">Needs approval</span> : null}
                </span>
              </td>
              <td data-label="Expires"><ExpiryText exception={e} /></td>
              <td data-label="Requested" className="small">{e.requested_by ?? "unknown"} · <Time value={e.requested_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExceptionList({ scope }: { scope: GovernanceScope }) {
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, scope.id]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const summary = useQuery({ queryKey: ["governance", scope.id, "exceptions-summary"], queryFn: () => getSecurityExceptions(scope.id) });
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id) });
  const repositories = useQuery({ queryKey: ["governance", scope.id, "repository-picker", "", { sort: "name" }], queryFn: () => listRepositoryMatrix(scope.id, { sort: "name", limit: 100 }) });
  const query = useQuery({
    queryKey: ["governance", scope.id, "exceptions", values, cursor],
    queryFn: () =>
      listExceptions(scope.id, {
        status: (values.status as ExceptionStatus) || undefined,
        rule: values.rule || undefined,
        repository: values.repository ? Number(values.repository) : null,
        group: values.group || undefined,
        cursor,
      }),
    placeholderData: (previous) => previous,
  });
  const counts = summary.data?.counts ?? {};
  const active = KEYS.filter((key) => values[key]).length;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organization}>{scope.login}</Link>}
        title="Exceptions"
        description="Scoped, time-limited exceptions that lower one rule to WARN or ALLOW. Detection continues and findings are still recorded; historical scan results never change."
        actions={
          scope.has("exceptions:create") ? (
            <Link className="button button--primary" to={routes.newException}>
              <Plus size={14} aria-hidden="true" /> Request an exception
            </Link>
          ) : null
        }
      />
      {summary.isPending ? (
        <SkeletonRows rows={1} label="Loading counts…" />
      ) : summary.data ? (
        <div className="metrics metrics--small exceptions__metrics">
          <Metric label="Requested" value={count(counts.requested ?? 0)} detail="waiting for approval" to={`${routes.exceptions}?status=requested`} />
          <Metric label="Active" value={count(counts.active ?? 0)} detail={`${count(counts.expiring_soon ?? 0)} expiring within 7 days`} to={`${routes.exceptions}?status=active`} />
          <Metric label="Expired" value={count(counts.expired ?? 0)} to={`${routes.exceptions}?status=expired`} />
          <Metric label="Revoked" value={count(counts.revoked ?? 0)} to={`${routes.exceptions}?status=revoked`} />
          <Metric label="Rejected or cancelled" value={count((counts.rejected ?? 0) + (counts.cancelled ?? 0))} />
        </div>
      ) : null}
      <FilterBar active={active} onClear={() => update({ status: null, rule: null, repository: null, group: null })}>
        <SelectField label="Status" value={values.status} onChange={(status) => update({ status })} options={EXCEPTION_STATUS_OPTIONS.map((s) => [s, statusStyle(EXCEPTION_STATUS, s).label] as [string, string])} />
        <SelectField label="Rule" value={values.rule} onChange={(rule) => update({ rule })} options={RULE_OPTIONS} />
        <SelectField
          label="Repository"
          value={values.repository}
          onChange={(repository) => update({ repository })}
          options={(repositories.data?.items ?? []).map((r) => [String(r.repository_id), r.full_name] as [string, string])}
        />
        <SelectField label="Group" value={values.group} onChange={(group) => update({ group })} options={(groups.data ?? []).map((g) => [g.id, g.name] as [string, string])} />
      </FilterBar>
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load exceptions."
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState title={active ? "No exceptions match these filters." : "No exceptions."}>
              {active ? "Clear the filters to see every exception." : "Every rule is enforced as policy requires."}
            </EmptyState>
          }
        >
          {(page) => (
            <>
              <ExceptionsTable items={page.items} />
              <Pagination label="Exceptions" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
      {values.repository ? (
        <p className="muted small">
          The repository filter lists exceptions scoped to that repository only. Group and organization exceptions that also apply to it are shown on its repository page, under Effective policy; use the group filter for a group&apos;s own exceptions.
        </p>
      ) : null}
    </>
  );
}

export default function Exceptions() {
  useDocumentTitle("Exceptions");
  return (
    <OrganizationGate title="Exceptions" permission="exceptions:read">
      {(scope) => <ExceptionList key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
