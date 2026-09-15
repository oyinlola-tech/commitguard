import { useQuery } from "@tanstack/react-query";

import { listRepositories } from "../api/repositories";
import { useSession } from "../auth/session";
import { FilterBar, SearchField, SelectField } from "../components/FilterBar";
import { Pagination, useCursorPager } from "../components/Pagination";
import { PageHeader, Panel } from "../components/Primitives";
import { EmptyState, QueryBoundary } from "../components/States";
import { RepositoriesTable } from "../components/Tables";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useUrlState } from "../hooks/useUrlState";

const KEYS = ["q", "protection", "sort"] as const;

export default function Repositories() {
  useDocumentTitle("Repositories");
  const { organization } = useSession();
  const { values, cursor, update } = useUrlState(KEYS);
  const pager = useCursorPager(JSON.stringify([values, organization]), cursor, (c) => update({ cursor: c }, { resetCursor: false }));
  const query = useQuery({
    queryKey: ["repositories", values, organization, cursor],
    queryFn: () => listRepositories({ ...values, organization, cursor }),
    placeholderData: (previous) => previous,
  });
  const active = [values.q, values.protection].filter(Boolean).length;
  return (
    <>
      <PageHeader title="Repositories" description="Repositories the CommitGuard GitHub App covers and you can access on GitHub." />
      <FilterBar active={active} onClear={() => update({ q: null, protection: null })}>
        <SearchField label="Search" value={values.q} onChange={(q) => update({ q })} placeholder="owner/name" />
        <SelectField
          label="Protection"
          value={values.protection}
          onChange={(protection) => update({ protection })}
          options={[["protected", "Protected"], ["unprotected", "Unprotected"], ["configuration_error", "Configuration error"], ["unknown", "Unknown"]]}
        />
        <SelectField label="Sort" anyLabel="Name" value={values.sort} onChange={(sort) => update({ sort })} options={[["risk", "Needs attention first"], ["recent", "Recently scanned"]]} />
      </FilterBar>
      <Panel flush>
        <QueryBoundary
          query={query}
          errorTitle="We could not load repositories."
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState title={active ? "No repositories match these filters." : "No repositories yet."}>
              {active ? "Clear the filters to see every repository." : "Install the CommitGuard GitHub App on a repository, then synchronize the installation."}
            </EmptyState>
          }
        >
          {(page) => (
            <>
              <RepositoriesTable repositories={page.items} />
              <Pagination label="Repositories" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
            </>
          )}
        </QueryBoundary>
      </Panel>
    </>
  );
}
