import { useQuery } from "@tanstack/react-query";

import { listScans } from "../api/scans";
import type { Page, ScanSummary } from "../api/types";
import { useSession } from "../auth/session";
import { DateField, FilterBar, SearchField, SelectField } from "../components/FilterBar";
import { Pagination, useCursorPager } from "../components/Pagination";
import { PageHeader, Panel } from "../components/Primitives";
import { EmptyState, QueryBoundary } from "../components/States";
import { ScansTable } from "../components/Tables";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { isInProgress, useBackoffPolling } from "../hooks/usePolling";
import { useUrlState } from "../hooks/useUrlState";
import { RESULT_OPTIONS, RULE_OPTIONS, SCAN_RESULT, SEVERITY, SEVERITY_OPTIONS, statusStyle } from "../lib/labels";

const KEYS = ["q", "result", "event", "rule", "severity", "from", "to", "repository", "sort"] as const;

const anyInProgress = (page: Page<ScanSummary> | undefined) => Boolean(page?.items.some((s) => isInProgress(s.result)));

export default function Scans() {
  useDocumentTitle("Scans");
  const { organization } = useSession();
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, organization]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const refetchInterval = useBackoffPolling(anyInProgress);
  const query = useQuery({
    queryKey: ["scans", values, organization, cursor],
    queryFn: () =>
      listScans({
        ...values,
        repository: values.repository ? Number(values.repository) : null,
        from: values.from ? `${values.from}T00:00:00Z` : undefined,
        to: values.to ? `${values.to}T23:59:59Z` : undefined,
        organization,
        cursor,
      }),
    placeholderData: (previous) => previous,
    refetchInterval,
  });
  const active = [values.q, values.result, values.event, values.rule, values.severity, values.from, values.to, values.repository].filter(Boolean).length;
  return (
    <>
      <PageHeader title="Scans" description="Every GitHub App scan, with the result CommitGuard core reached." />
      <FilterBar active={active} onClear={() => update({ q: null, result: null, event: null, rule: null, severity: null, from: null, to: null, repository: null })}>
        <SearchField label="Repository or commit" value={values.q} onChange={(q) => update({ q })} placeholder="owner/name or SHA" />
        <SelectField label="Result" value={values.result} onChange={(result) => update({ result })} options={RESULT_OPTIONS.map((r) => [r, statusStyle(SCAN_RESULT, r).label] as [string, string])} />
        <SelectField label="Event" value={values.event} onChange={(event) => update({ event })} options={[["pull_request", "Pull request"], ["push", "Push"]]} />
        <SelectField label="Rule" value={values.rule} onChange={(rule) => update({ rule })} options={RULE_OPTIONS} />
        <SelectField label="Severity" value={values.severity} onChange={(severity) => update({ severity })} options={SEVERITY_OPTIONS.map((s) => [s, statusStyle(SEVERITY, s).label] as [string, string])} />
        <DateField label="From" value={values.from} onChange={(from) => update({ from })} />
        <DateField label="To" value={values.to} onChange={(to) => update({ to })} />
        <SelectField label="Order" anyLabel="Newest first" value={values.sort} onChange={(sort) => update({ sort })} options={[["oldest", "Oldest first"]]} />
      </FilterBar>
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load scans."
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState title={active ? "No scans match these filters." : "No scans yet."}>
              {active ? "Clear the filters to see all scans." : "Connect a GitHub repository to begin monitoring."}
            </EmptyState>
          }
        >
          {(page) => (
            <>
              <ScansTable scans={page.items} />
              <Pagination label="Scans" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
