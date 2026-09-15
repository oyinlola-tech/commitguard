import { useQuery } from "@tanstack/react-query";

import { listViolations } from "../api/violations";
import { useSession } from "../auth/session";
import { DateField, FilterBar, SearchField, SelectField } from "../components/FilterBar";
import { Pagination, useCursorPager } from "../components/Pagination";
import { PageHeader, Panel } from "../components/Primitives";
import { EmptyState, QueryBoundary } from "../components/States";
import { ViolationsTable } from "../components/Tables";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useUrlState } from "../hooks/useUrlState";
import { RULE_OPTIONS, SEVERITY, SEVERITY_OPTIONS, VIOLATION_STATUS } from "../lib/labels";

const KEYS = ["q", "status", "severity", "rule", "action", "repository", "from", "to", "sort"] as const;

export default function Violations() {
  useDocumentTitle("Violations");
  const { organization } = useSession();
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, organization]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const query = useQuery({
    queryKey: ["violations", values, organization, cursor],
    queryFn: () =>
      listViolations({
        ...values,
        repository: values.repository ? Number(values.repository) : null,
        from: values.from ? `${values.from}T00:00:00Z` : undefined,
        to: values.to ? `${values.to}T23:59:59Z` : undefined,
        organization,
        cursor,
      }),
    placeholderData: (previous) => previous,
  });
  const active = [values.q, values.status, values.severity, values.rule, values.action, values.repository, values.from, values.to].filter(Boolean).length;
  return (
    <>
      <PageHeader title="Violations" description="Findings a policy blocked or warned about, tracked across scans until they are no longer present." />
      <FilterBar active={active} onClear={() => update({ q: null, status: null, severity: null, rule: null, action: null, repository: null, from: null, to: null })}>
        <SearchField label="Search" value={values.q} onChange={(q) => update({ q })} placeholder="Rule, SHA, author or repository" />
        <SelectField label="Status" value={values.status} onChange={(status) => update({ status })} options={["open", "acknowledged", "resolved"].map((s) => [s, VIOLATION_STATUS[s]!.label] as [string, string])} />
        <SelectField label="Severity" value={values.severity} onChange={(severity) => update({ severity })} options={SEVERITY_OPTIONS.map((s) => [s, SEVERITY[s]!.label] as [string, string])} />
        <SelectField label="Rule" value={values.rule} onChange={(rule) => update({ rule })} options={RULE_OPTIONS} />
        <SelectField label="Policy action" value={values.action} onChange={(action) => update({ action })} options={[["block", "BLOCK"], ["warn", "WARN"]]} />
        <DateField label="Detected from" value={values.from} onChange={(from) => update({ from })} />
        <DateField label="Detected to" value={values.to} onChange={(to) => update({ to })} />
        <SelectField label="Sort" anyLabel="Newest first" value={values.sort} onChange={(sort) => update({ sort })} options={[["oldest", "Oldest first"], ["severity", "Severity"], ["repository", "Repository"]]} />
      </FilterBar>
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load violations."
          isEmpty={(page) => page.items.length === 0}
          empty={<EmptyState title={active ? "No violations match these filters." : "No violations detected."}>{active ? "Clear the filters to see all violations." : "Violations appear here when a scan finds a blocked or warned commit."}</EmptyState>}
        >
          {(page) => (
            <>
              <ViolationsTable violations={page.items} />
              <Pagination label="Violations" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
