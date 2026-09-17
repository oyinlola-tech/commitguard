import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, FolderMinus, FolderPlus, Pause, Pencil, Play, Plus, ScanLine, ShieldCheck, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { listBulkOperations } from "../api/bulk";
import { listRepositoryMatrix, MATRIX_FILTERS } from "../api/governance";
import { createGroup, listGroups } from "../api/groups";
import type { RepositoryGroup, RepositoryPosture } from "../api/types";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { BulkActionDialog, BulkProgress, type BulkAction } from "../components/BulkOperation";
import { FilterBar, SearchField, SelectField } from "../components/FilterBar";
import { ArchiveGroupDialog, RenameGroupDialog } from "../components/GroupDialogs";
import { OrganizationGate } from "../components/OrganizationGate";
import { Pagination, useCursorPager } from "../components/Pagination";
import { PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { useUrlState } from "../hooks/useUrlState";
import { count, plural } from "../lib/format";
import {
  APP_CONNECTION,
  BULK_STATUS,
  BULK_TYPE_LABEL,
  DRIFT,
  ONBOARDING,
  POSTURE,
  PROPAGATION,
  PROTECTION,
  REPOSITORY_MODE,
  SCAN_RESULT,
} from "../lib/labels";
import { routes } from "../lib/routes";

const KEYS = [...MATRIX_FILTERS, "operation"] as const;
const FILTER_KEYS = MATRIX_FILTERS.filter((key) => key !== "sort");

function PostureCell({ row }: { row: RepositoryPosture }) {
  return (
    <details className="cell-details">
      <summary>
        <Badge map={POSTURE} value={row.posture} compact />
        <span className="visually-hidden"> Why</span>
      </summary>
      <ul className="reasons small">
        {row.posture_reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
    </details>
  );
}

function DriftCell({ row }: { row: RepositoryPosture }) {
  if (row.drift !== "drift" || row.drift_differences.length === 0) return <Badge map={DRIFT} value={row.drift} compact />;
  return (
    <details className="cell-details">
      <summary>
        <Badge map={DRIFT} value={row.drift} compact />
        <span className="visually-hidden"> Differences</span>
      </summary>
      <div className="drift small">
        <p>The repository asks for less than a mandatory requirement. The requirement still applies.</p>
        <ul className="plain-list">
          {row.drift_differences.map((d) => (
            <li key={`${d.policy_id}-${d.required_by}`} className="drift__item">
              <code>{d.policy_id}</code>
              <span>
                {d.required_by}: <strong>{d.required.toUpperCase()}</strong>
              </span>
              <span>
                {d.requested_by}: <strong>{d.requested.toUpperCase()}</strong>
              </span>
              <span>
                Effective: <strong>{d.effective.toUpperCase()}</strong>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

function MatrixTable({
  rows,
  selectable,
  selected,
  onSelect,
}: {
  rows: RepositoryPosture[];
  selectable: boolean;
  selected: Map<number, string>;
  onSelect: (selected: Map<number, string>) => void;
}) {
  const header = useRef<HTMLInputElement>(null);
  const onPage = rows.filter((r) => selected.has(r.repository_id)).length;
  useEffect(() => {
    if (header.current) header.current.indeterminate = onPage > 0 && onPage < rows.length;
  }, [onPage, rows.length]);
  const toggle = (row: RepositoryPosture, checked: boolean) => {
    const next = new Map(selected);
    if (checked) next.set(row.repository_id, row.full_name);
    else next.delete(row.repository_id);
    onSelect(next);
  };
  const togglePage = (checked: boolean) => {
    const next = new Map(selected);
    for (const row of rows) {
      if (checked) next.set(row.repository_id, row.full_name);
      else next.delete(row.repository_id);
    }
    onSelect(next);
  };
  return (
    <div className="table-wrap">
      <table className="table matrix">
        <caption className="visually-hidden">Repository security matrix</caption>
        <thead>
          <tr>
            {selectable ? (
              <th scope="col" className="matrix__select">
                <input ref={header} type="checkbox" aria-label="Select every repository on this page" checked={rows.length > 0 && onPage === rows.length} onChange={(e) => togglePage(e.target.checked)} />
              </th>
            ) : null}
            <th scope="col">Repository</th>
            <th scope="col">Posture</th>
            <th scope="col">Protection</th>
            <th scope="col">State</th>
            <th scope="col">Groups</th>
            <th scope="col">Policy and drift</th>
            <th scope="col">Last scan</th>
            <th scope="col">Findings and exceptions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.repository_id} className={selected.has(row.repository_id) ? "table__row--current" : undefined}>
              {selectable ? (
                <td data-label="Select" className="matrix__select">
                  <input type="checkbox" aria-label={`Select ${row.full_name}`} checked={selected.has(row.repository_id)} onChange={(e) => toggle(row, e.target.checked)} />
                </td>
              ) : null}
              <td data-label="Repository" className="table__primary">
                <Link to={routes.repository(row.repository_id)} className="strong">
                  {row.full_name}
                </Link>
                {row.archived ? <span className="tag">Archived</span> : null}
              </td>
              <td data-label="Posture"><PostureCell row={row} /></td>
              <td data-label="Protection"><Badge map={PROTECTION} value={row.protection} compact title={row.protection_reason} /></td>
              <td data-label="State">
                <span className="matrix__state">
                  <Badge map={APP_CONNECTION} value={row.connection} compact />
                  <Badge map={REPOSITORY_MODE} value={row.mode} compact />
                  <Badge map={ONBOARDING} value={row.onboarding} compact />
                </span>
              </td>
              <td data-label="Groups">
                {row.groups.length ? (
                  <span className="link-list">
                    {row.groups.map((g) => (
                      <Link key={g.id} to={routes.group(g.id)}>{g.name}</Link>
                    ))}
                  </span>
                ) : (
                  <span className="muted">None</span>
                )}
              </td>
              <td data-label="Policy and drift">
                <span className="stack">
                  <Badge map={PROPAGATION} value={row.policy_state} compact />
                  <DriftCell row={row} />
                  <span className="muted small nowrap" title="Organization policy version recorded by the latest scan">{row.organization_policy_version ? `Org. v${row.organization_policy_version} at scan` : "No org. policy"}</span>
                </span>
              </td>
              <td data-label="Last scan">
                {row.last_scan_at ? (
                  <span className="stack">
                    {row.last_scan_result ? <Badge map={SCAN_RESULT} value={row.last_scan_result} compact /> : null}
                    <span className="small nowrap"><Time value={row.last_scan_at} /></span>
                  </span>
                ) : (
                  <span className="muted">Never</span>
                )}
              </td>
              <td data-label="Findings and exceptions">
                <span className="stack small nowrap">
                  <span className={row.open_violations ? "strong" : undefined}>{count(row.open_violations)} open</span>
                  <span>
                    <span className={row.critical_open ? "text-critical strong" : undefined}>{count(row.critical_open)} critical</span>
                    {" · "}
                    {count(row.open_warnings)} warn.
                  </span>
                  <span>
                    {plural(row.active_exceptions, "exception")}
                    {row.expiring_exceptions ? <span className="tag tag--warning">{count(row.expiring_exceptions)} expiring</span> : null}
                  </span>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SelectionBar({
  scope,
  selected,
  onAction,
  onClear,
}: {
  scope: GovernanceScope;
  selected: Map<number, string>;
  onAction: (action: BulkAction) => void;
  onClear: () => void;
}) {
  const manage = scope.has("repositories:manage");
  return (
    <section className="selection-bar" aria-label="Bulk actions">
      <p className="selection-bar__count" aria-live="polite">
        {plural(selected.size, "repository", "repositories")} selected
      </p>
      <div className="selection-bar__actions">
        {manage ? (
          <>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "add_to_group" })}>
              <FolderPlus size={14} aria-hidden="true" /> Add to group
            </button>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "remove_from_group" })}>
              <FolderMinus size={14} aria-hidden="true" /> Remove from group
            </button>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "onboard" })}>
              <ShieldCheck size={14} aria-hidden="true" /> Onboard
            </button>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "set_mode" })}>
              Change mode
            </button>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "set_monitoring", enabled: false })}>
              <Pause size={14} aria-hidden="true" /> Pause monitoring
            </button>
            <button type="button" className="button button--secondary" onClick={() => onAction({ type: "set_monitoring", enabled: true })}>
              <Play size={14} aria-hidden="true" /> Resume monitoring
            </button>
          </>
        ) : null}
        {scope.has("scans:trigger") ? (
          <button type="button" className="button button--secondary" onClick={() => onAction({ type: "schedule_scan" })}>
            <ScanLine size={14} aria-hidden="true" /> Scan default branch
          </button>
        ) : null}
        <button type="button" className="button button--ghost" onClick={onClear}>
          <X size={14} aria-hidden="true" /> Clear selection
        </button>
      </div>
    </section>
  );
}

function RecentOperations({ scope, onShow }: { scope: GovernanceScope; onShow: (id: string) => void }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "bulk-operations"], queryFn: () => listBulkOperations(scope.id) });
  return (
    <QueryBoundary
      query={query}
      errorTitle="We could not load bulk operations."
      loading={<SkeletonRows rows={2} />}
      isEmpty={(items) => items.length === 0}
      empty={<p className="muted small panel__inset">No bulk operations yet.</p>}
    >
      {(items) => (
        <div className="table-wrap">
          <table className="table">
            <caption className="visually-hidden">Recent bulk operations</caption>
            <thead>
              <tr><th scope="col">Operation</th><th scope="col">Status</th><th scope="col">Progress</th><th scope="col">Requested</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
            </thead>
            <tbody>
              {items.slice(0, 10).map((operation) => (
                <tr key={operation.id}>
                  <td data-label="Operation" className="table__primary">{BULK_TYPE_LABEL[operation.type] ?? operation.type} · {plural(operation.total, "repository", "repositories")}</td>
                  <td data-label="Status"><Badge map={BULK_STATUS} value={operation.status} compact /></td>
                  <td data-label="Progress" className="small">{count(operation.completed)} completed · {count(operation.failed)} failed · {count(operation.skipped)} skipped · {count(operation.pending)} pending</td>
                  <td data-label="Requested" className="small">{operation.requested_by ?? "unknown"} · <Time value={operation.created_at} /></td>
                  <td data-label="Actions" className="align-end">
                    <button type="button" className="button button--ghost" onClick={() => onShow(operation.id)}>
                      Details<span className="visually-hidden"> of this operation</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </QueryBoundary>
  );
}

function GroupsPanel({ scope }: { scope: GovernanceScope }) {
  const queryClient = useQueryClient();
  const manage = scope.has("repositories:manage");
  const [archived, setArchived] = useState(false);
  const query = useQuery({ queryKey: ["governance", scope.id, "groups", archived], queryFn: () => listGroups(scope.id, archived) });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [renaming, setRenaming] = useState<RepositoryGroup | null>(null);
  const [archiving, setArchiving] = useState<RepositoryGroup | null>(null);
  const create = useMutation({
    mutationFn: () => createGroup(scope.id, name.trim(), description.trim() || null),
    onSuccess: () => {
      setName("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id] });
    },
  });
  return (
    <>
      <div className="panel__inset panel__toolbar">
        <p className="muted small">A group&apos;s policy applies to every repository in it. A repository may belong to several groups: mandatory requirements combine to the strongest, and the most restrictive default applies.</p>
        <label className="checkbox checkbox--inline">
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Include archived groups
        </label>
      </div>
      <QueryBoundary
        query={query}
        errorTitle="We could not load repository groups."
        loading={<SkeletonRows rows={2} />}
        isEmpty={(groups) => groups.length === 0}
        empty={<EmptyState title="No repository groups yet.">Groups let you apply a policy, an exception or a scan schedule to a set of repositories.</EmptyState>}
      >
        {(groups) => (
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Repository groups</caption>
              <thead>
                <tr><th scope="col">Group</th><th scope="col">Repositories</th><th scope="col">Policy</th><th scope="col">Active exceptions</th><th scope="col">Created</th><th scope="col"><span className="visually-hidden">Actions</span></th></tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <tr key={group.id}>
                    <td data-label="Group" className="table__primary">
                      <span className="stack">
                        <Link to={routes.group(group.id)} className="strong break">{group.name}</Link>
                        {group.description ? <span className="muted small break">{group.description}</span> : null}
                      </span>
                      {group.archived_at ? <span className="tag">Archived</span> : null}
                    </td>
                    <td data-label="Repositories">{count(group.repository_count)}</td>
                    <td data-label="Policy">{group.policy_version ? `v${group.policy_version}` : <span className="muted">No policy</span>}</td>
                    <td data-label="Active exceptions">{count(group.active_exceptions)}</td>
                    <td data-label="Created" className="small">{group.created_by ?? "unknown"} · <Time value={group.created_at} /></td>
                    <td data-label="Actions" className="align-end">
                      {manage && !group.archived_at ? (
                        <span className="row-actions">
                          <button type="button" className="icon-button" aria-label={`Rename ${group.name}`} title="Rename" onClick={() => setRenaming(group)}>
                            <Pencil size={14} aria-hidden="true" />
                          </button>
                          <button type="button" className="icon-button" aria-label={`Archive ${group.name}`} title="Archive" onClick={() => setArchiving(group)}>
                            <Archive size={14} aria-hidden="true" />
                          </button>
                        </span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryBoundary>
      {manage ? (
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <div className="field">
            <label htmlFor="new-group-name">New group name</label>
            <input id="new-group-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field field--grow">
            <label htmlFor="new-group-description">Description (optional)</label>
            <input id="new-group-description" maxLength={500} value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          <button type="submit" className="button button--primary" disabled={create.isPending || !name.trim()}>
            <Plus size={14} aria-hidden="true" /> Create group
          </button>
          <div className="field--full">
            <ActionError error={create.error} fallback="The group could not be created." />
          </div>
        </form>
      ) : null}
      {renaming ? <RenameGroupDialog group={renaming} onClose={() => setRenaming(null)} /> : null}
      {archiving ? <ArchiveGroupDialog group={archiving} onClose={() => setArchiving(null)} /> : null}
    </>
  );
}

const SELECT_OPTIONS = {
  posture: [["at_risk", "At risk"], ["unprotected", "Unprotected"], ["unknown", "Unknown"], ["secure", "Secure"]],
  protection: [["protected", "Protected"], ["at_risk", "At risk"], ["unprotected", "Unprotected"], ["configuration_error", "Configuration error"], ["unknown", "Unknown"]],
  mode: [["enforce", "Enforce"], ["monitor", "Monitor"]],
  onboarding: [["discovered", "Discovered"], ["onboarded", "Onboarded"], ["excluded", "Excluded"]],
  policy_state: [["up_to_date", "Up to date"], ["stale", "Stale"], ["syncing", "Syncing"], ["error", "Error"], ["pending", "Pending"]],
  drift: [["drift", "Drift"], ["customized", "Customized"], ["compliant", "Compliant"], ["unknown", "Unknown"]],
  exceptions: [["active", "Active"], ["expiring", "Expiring within 7 days"], ["none", "None"]],
  severity: [["critical", "Open critical"], ["any", "Any open finding"]],
  last_scan: [["never", "Never scanned"], ["7", "Not scanned in 7 days"], ["30", "Not scanned in 30 days"], ["90", "Not scanned in 90 days"]],
} satisfies Record<string, [string, string][]>;

function Matrix({ scope }: { scope: GovernanceScope }) {
  const { values, cursor, update } = useUrlState(KEYS);
  const filters = Object.fromEntries(MATRIX_FILTERS.map((key) => [key, values[key] || undefined]));
  const pager = useCursorPager(JSON.stringify([filters, scope.id]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const query = useQuery({
    queryKey: ["governance", scope.id, "matrix", filters, cursor],
    queryFn: () => listRepositoryMatrix(scope.id, { ...filters, cursor }),
    placeholderData: (previous) => previous,
  });
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id) });
  const [selected, setSelected] = useState<Map<number, string>>(new Map());
  const [action, setAction] = useState<BulkAction | null>(null);
  const selectable = scope.has("repositories:manage") || scope.has("scans:trigger");
  const active = FILTER_KEYS.filter((key) => values[key]).length;
  const clear = () => update(Object.fromEntries(FILTER_KEYS.map((key) => [key, null])));
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organization}>{scope.login}</Link>}
        title="Repository security matrix"
        description="Every repository of the organization with its posture, enforcement, mode, policy propagation, findings, exceptions and drift. Each column is a separate server-computed state."
        actions={
          scope.has("repositories:manage") ? (
            <Link className="button button--primary" to={routes.addRepositories}>
              <Plus size={14} aria-hidden="true" /> Add repositories
            </Link>
          ) : null
        }
      />
      {values.operation ? (
        <Panel title="Bulk operation" id="bulk-operation">
          <BulkProgress key={values.operation} operationId={values.operation} onDismiss={() => update({ operation: null }, { resetCursor: false })} />
        </Panel>
      ) : null}
      <FilterBar active={active} onClear={clear}>
        <SearchField label="Search" value={values.q} onChange={(q) => update({ q })} placeholder="owner/name" />
        <SelectField label="Group" value={values.group} onChange={(group) => update({ group })} options={(groups.data ?? []).map((g) => [g.id, g.name] as [string, string])} />
        <SelectField label="Posture" value={values.posture} onChange={(posture) => update({ posture })} options={SELECT_OPTIONS.posture} />
        <SelectField label="Protection" value={values.protection} onChange={(protection) => update({ protection })} options={SELECT_OPTIONS.protection} />
        <SelectField label="Mode" value={values.mode} onChange={(mode) => update({ mode })} options={SELECT_OPTIONS.mode} />
        <SelectField label="Onboarding" value={values.onboarding} onChange={(onboarding) => update({ onboarding })} options={SELECT_OPTIONS.onboarding} />
        <SelectField label="Policy propagation" value={values.policy_state} onChange={(policy_state) => update({ policy_state })} options={SELECT_OPTIONS.policy_state} />
        <SelectField label="Drift" value={values.drift} onChange={(drift) => update({ drift })} options={SELECT_OPTIONS.drift} />
        <SelectField label="Exceptions" value={values.exceptions} onChange={(exceptions) => update({ exceptions })} options={SELECT_OPTIONS.exceptions} />
        <SelectField label="Findings" value={values.severity} onChange={(severity) => update({ severity })} options={SELECT_OPTIONS.severity} />
        <SelectField label="Last scan" value={values.last_scan} onChange={(last_scan) => update({ last_scan })} options={SELECT_OPTIONS.last_scan} />
        <SelectField label="Sort" anyLabel="Needs attention first" value={values.sort} onChange={(sort) => update({ sort })} options={[["name", "Name"], ["violations", "Most violations"], ["last_scan", "Least recently scanned"]]} />
      </FilterBar>
      {selected.size > 0 && selectable ? <SelectionBar scope={scope} selected={selected} onAction={setAction} onClear={() => setSelected(new Map())} /> : null}
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load the repository matrix."
          loading={<SkeletonRows rows={6} label="Loading repositories…" />}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState title={active ? "No repositories match these filters." : "No repositories yet."}>
              {active ? "Clear the filters to see every repository." : "Install the CommitGuard GitHub App on repositories of this organization."}
            </EmptyState>
          }
        >
          {(page) => (
            <>
              <p className="panel__inset muted small matrix__summary">
                {plural(page.total, "repository", "repositories")}
                {active ? " match" : ""} · computed <Time value={page.computedAt} />
              </p>
              <MatrixTable rows={page.items} selectable={selectable} selected={selected} onSelect={setSelected} />
              <Pagination label="Repositories" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
      <Panel title="Repository groups" id="groups" flush>
        <GroupsPanel scope={scope} />
      </Panel>
      <Panel title="Recent bulk operations" id="bulk-operations" flush>
        <RecentOperations scope={scope} onShow={(id) => update({ operation: id }, { resetCursor: false })} />
      </Panel>
      {action ? (
        <BulkActionDialog
          organization={scope.id}
          action={action}
          repositories={selected}
          onClose={() => setAction(null)}
          onCreated={(operation) => {
            setAction(null);
            setSelected(new Map());
            update({ operation: operation.id }, { resetCursor: false });
          }}
        />
      ) : null}
    </>
  );
}

export default function OrganizationRepositories() {
  useDocumentTitle("Repository matrix");
  return (
    <OrganizationGate title="Repository security matrix" permission="security:read">
      {(scope) => <Matrix key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
