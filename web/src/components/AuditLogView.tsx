import { useQuery } from "@tanstack/react-query";

import { listAudit } from "../api/audit";
import { useUrlState } from "../hooks/useUrlState";
import { humanize } from "../lib/format";
import { GOVERNANCE_AUDIT_TYPES, RULE_OPTIONS } from "../lib/labels";
import { AuditTable } from "./AuditTable";
import { DateField, FilterBar, SearchField, SelectField } from "./FilterBar";
import { Pagination, useCursorPager } from "./Pagination";
import { Panel } from "./Primitives";
import { EmptyState, QueryBoundary } from "./States";

export const AUDIT_TYPES = [
  "organization_policy_changed", "organization_policy_rolled_back", "policy_violation", "policy_modification", "scan_failed", "scan_passed", "scan_error", "scan_requested",
  "violation_opened", "violation_reopened", "violation_resolved", "violation_acknowledged", "violation_acknowledgement_removed",
  "repository_monitoring_disabled", "repository_monitoring_enabled", "enforcement_status_checked",
  "installation_created", "installation_removed", "installation_suspended", "installation_unsuspended", "installation_permissions_updated",
  "repositories_added", "repositories_removed", "repositories_synced",
  "member_role_granted", "member_role_changed", "member_removed", "user_signed_in", "user_signed_out", "session_revoked",
  "configuration_error", "authorization_denied", "webhook_rejected",
  ...GOVERNANCE_AUDIT_TYPES,
] as const;

const KEYS = ["type", "actor", "repository", "from", "to", "sort", "rule", "exception", "policy"] as const;
const HEX_OR_ORGANIZATION = /^([0-9a-f]{32}|organization)$/;

/**
 * The audit log with server-side filters in the URL. `organization` narrows it
 * to one organization; `governance` adds the rule, exception and policy filters.
 */
export function AuditLogView({ organization, governance = false }: { organization: number | null; governance?: boolean }) {
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, organization]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const exception = /^[0-9a-f]{32}$/.test(values.exception) ? values.exception : undefined;
  const policy = HEX_OR_ORGANIZATION.test(values.policy) ? values.policy : undefined;
  const query = useQuery({
    queryKey: ["audit", values, organization, cursor],
    queryFn: () =>
      listAudit({
        type: values.type,
        actor: values.actor,
        sort: values.sort,
        repository: values.repository ? Number(values.repository) : null,
        from: values.from ? `${values.from}T00:00:00Z` : undefined,
        to: values.to ? `${values.to}T23:59:59Z` : undefined,
        rule: governance ? values.rule || undefined : undefined,
        exception: governance ? exception : undefined,
        policy: governance ? policy : undefined,
        organization,
        cursor,
      }),
    placeholderData: (previous) => previous,
  });
  const filters: (typeof KEYS)[number][] = governance ? ["type", "actor", "repository", "from", "to", "rule", "exception", "policy"] : ["type", "actor", "repository", "from", "to"];
  const active = filters.filter((key) => values[key]).length;
  return (
    <>
      <FilterBar active={active} onClear={() => update(Object.fromEntries(filters.map((key) => [key, null])))}>
        <SelectField label="Action" value={values.type} onChange={(type) => update({ type })} options={AUDIT_TYPES.map((t) => [t, humanize(t)] as [string, string])} />
        <SearchField label="Actor" value={values.actor} onChange={(actor) => update({ actor })} placeholder="GitHub login" />
        {governance ? (
          <>
            <SelectField label="Rule" value={values.rule} onChange={(rule) => update({ rule })} options={RULE_OPTIONS} />
            <SearchField label="Repository ID" value={values.repository} onChange={(repository) => update({ repository: /^\d{1,16}$/.test(repository) ? repository : null })} placeholder="5001" />
            <SearchField label="Exception ID" value={values.exception} onChange={(value) => update({ exception: value || null })} placeholder="32 hexadecimal characters" />
            <SearchField label="Policy" value={values.policy} onChange={(value) => update({ policy: value || null })} placeholder="organization, or a draft, group or rollout ID" />
          </>
        ) : null}
        <DateField label="From" value={values.from} onChange={(from) => update({ from })} />
        <DateField label="To" value={values.to} onChange={(to) => update({ to })} />
        <SelectField label="Order" anyLabel="Newest first" value={values.sort} onChange={(sort) => update({ sort })} options={[["oldest", "Oldest first"]]} />
      </FilterBar>
      {governance && ((values.exception && !exception) || (values.policy && !policy)) ? (
        <p className="muted small">An exception filter must be an exception ID, and a policy filter “organization” or a draft, group or rollout ID. Invalid values are ignored.</p>
      ) : null}
      <Panel flush>
        <QueryBoundary query={query} errorTitle="We could not load the audit log." isEmpty={(p) => p.items.length === 0} empty={<EmptyState title={active ? "No events match these filters." : "No audit events yet."} />}>
          {(page) => (
            <>
              <AuditTable events={page.items} caption="Audit events" />
              <Pagination label="Audit events" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
