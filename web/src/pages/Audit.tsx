import { useQuery } from "@tanstack/react-query";
import { Lock } from "lucide-react";

import { listAudit } from "../api/audit";
import { useSession } from "../auth/session";
import { AuditTable } from "../components/AuditTable";
import { DateField, FilterBar, SearchField, SelectField } from "../components/FilterBar";
import { Pagination, useCursorPager } from "../components/Pagination";
import { PageHeader, Panel } from "../components/Primitives";
import { EmptyState, QueryBoundary } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useUrlState } from "../hooks/useUrlState";
import { humanize } from "../lib/format";

const KEYS = ["type", "actor", "repository", "from", "to", "sort"] as const;

const TYPES = [
  "organization_policy_changed", "policy_violation", "policy_modification", "scan_failed", "scan_passed", "scan_error", "scan_requested",
  "violation_opened", "violation_reopened", "violation_resolved", "violation_acknowledged", "violation_acknowledgement_removed",
  "repository_monitoring_disabled", "repository_monitoring_enabled", "enforcement_status_checked",
  "installation_created", "installation_removed", "installation_suspended", "installation_unsuspended", "installation_permissions_updated",
  "repositories_added", "repositories_removed", "repositories_synced",
  "member_role_granted", "member_role_changed", "member_removed", "user_signed_in", "user_signed_out", "session_revoked",
  "configuration_error", "authorization_denied", "webhook_rejected",
] as const;

export default function Audit() {
  useDocumentTitle("Audit log");
  const { organization } = useSession();
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, organization]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const query = useQuery({
    queryKey: ["audit", values, organization, cursor],
    queryFn: () =>
      listAudit({
        ...values,
        repository: values.repository ? Number(values.repository) : null,
        from: values.from ? `${values.from}T00:00:00Z` : undefined,
        to: values.to ? `${values.to}T23:59:59Z` : undefined,
        organization,
        cursor,
      }),
    placeholderData: (previous) => previous,
  });
  const active = [values.type, values.actor, values.repository, values.from, values.to].filter(Boolean).length;
  return (
    <>
      <PageHeader
        title="Audit log"
        description={<><Lock size={12} aria-hidden="true" /> Security-relevant actions, recorded when they happen. Events cannot be edited or deleted here; retention removes them after the configured period.</>}
      />
      <FilterBar active={active} onClear={() => update({ type: null, actor: null, repository: null, from: null, to: null })}>
        <SelectField label="Action" value={values.type} onChange={(type) => update({ type })} options={TYPES.map((t) => [t, humanize(t)] as [string, string])} />
        <SearchField label="Actor" value={values.actor} onChange={(actor) => update({ actor })} placeholder="GitHub login" />
        <DateField label="From" value={values.from} onChange={(from) => update({ from })} />
        <DateField label="To" value={values.to} onChange={(to) => update({ to })} />
        <SelectField label="Order" anyLabel="Newest first" value={values.sort} onChange={(sort) => update({ sort })} options={[["oldest", "Oldest first"]]} />
      </FilterBar>
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
