import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { getInstallation, listInstallationRepositories, syncInstallation } from "../api/github";
import type { InstallationRepository } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { Badge } from "../components/Badge";
import { DataTable } from "../components/DataTable";
import { Pagination, useCursorPager } from "../components/Pagination";
import { ExternalLink, KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, ErrorState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { APP_CONNECTION } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const REQUIRED = new Set(["checks: write", "contents: read", "metadata: read", "pull_requests: read"]);

function Repositories({ installationId }: { installationId: number }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const pager = useCursorPager(String(installationId), cursor, setCursor);
  const query = useQuery({
    queryKey: ["installation-repositories", installationId, cursor],
    queryFn: () => listInstallationRepositories(installationId, cursor),
    placeholderData: (previous) => previous,
  });
  return (
    <QueryBoundary query={query} errorTitle="We could not load repositories." isEmpty={(p) => p.items.length === 0} empty={<EmptyState title="No repositories you can access." />}>
      {(page) => (
        <>
          <DataTable<InstallationRepository>
            caption="Installation repositories"
            rows={page.items}
            rowKey={(r) => r.id}
            rowHref={(r) => routes.repository(r.id)}
            columns={[
              { key: "name", header: "Repository", primary: true, cell: (r) => <span className="strong">{r.full_name}</span> },
              { key: "connected", header: "GitHub App", cell: (r) => <Badge map={APP_CONNECTION} value={r.connected ? "connected" : "disconnected"} compact /> },
              { key: "monitoring", header: "Monitoring", cell: (r) => (r.monitoring_enabled ? "Enabled" : "Paused") },
              { key: "added", header: "First seen", hideOnMobile: true, cell: (r) => <Time value={r.added_at} /> },
            ]}
          />
          <Pagination label="Repositories" page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} />
        </>
      )}
    </QueryBoundary>
  );
}

export default function InstallationDetail() {
  const { installationId = "" } = useParams();
  const id = Number(installationId);
  const valid = /^\d{1,16}$/.test(installationId) && id > 0;
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["installation", id], queryFn: () => getInstallation(id), enabled: valid });
  useDocumentTitle(query.data ? `${query.data.installation.account.login} installation` : "Installation");
  const sync = useMutation({
    mutationFn: () => syncInstallation(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["installation", id] });
      void queryClient.invalidateQueries({ queryKey: ["installation-repositories", id] });
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
    },
  });

  if (!valid) return <NotFoundContent resource="installation" />;
  if (query.isPending) return <SkeletonRows rows={6} label="Loading installation…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="installation" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this installation." error={query.error} onRetry={() => void query.refetch()} />;

  const { installation, recent_events } = query.data;
  const result = sync.data?.data;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.installations}>GitHub installations</Link>}
        title={installation.account.login}
        description={`${installation.account.type} · installation ${installation.id}`}
        actions={
          installation.can_manage ? (
            <button type="button" className="button button--secondary" onClick={() => sync.mutate()} disabled={sync.isPending}>
              <RefreshCw size={14} aria-hidden="true" className={sync.isPending ? "spin" : undefined} /> {sync.isPending ? "Synchronizing…" : "Sync repositories"}
            </button>
          ) : null
        }
      />
      {result ? (
        <Notice tone="success" title="Repositories synchronized">
          {result.repositories} repositories · {result.added.length} added · {result.removed.length} removed. Repositories added to GitHub appear in your view after you sign in again.
        </Notice>
      ) : null}
      {sync.error ? <Notice tone="danger">{sync.error instanceof ApiError ? sync.error.message : "Synchronization failed."}</Notice> : null}

      <div className="grid-2">
        <Panel title="Installation" id="installation">
          <KeyValueList
            items={[
              ["GitHub account", installation.account.login],
              ["Account type", installation.account.type],
              ["Status", <Badge map={APP_CONNECTION} value={installation.status} compact />],
              ["Repository access", installation.repository_selection === "all" ? "All repositories" : "Selected repositories"],
              ["Repositories", String(installation.repositories)],
              ["Installed", <Time value={installation.installed_at} absolute />],
              ["Last event", <Time value={installation.last_event_at} />],
            ]}
          />
          <p className="muted small">
            Installing, suspending and uninstalling the App happens on GitHub. CommitGuard reflects those changes when GitHub notifies it. <ExternalLink href={installation.github_settings_url}>Manage on GitHub</ExternalLink>
          </p>
        </Panel>
        <Panel title="Permissions" id="permissions">
          {installation.missing_permissions.length ? (
            <Notice tone="danger" title="Missing permissions">
              {installation.missing_permissions.join(", ")}. Scans for this installation fail until an owner approves them on GitHub.
            </Notice>
          ) : null}
          <ul className="permissions">
            {Object.entries(installation.permissions).sort().map(([name, level]) => {
              const label = `${name}: ${level}`;
              const excessive = installation.excessive_permissions.includes(label);
              return (
                <li key={name}>
                  <code>{label}</code>
                  <span className={excessive ? "text-warning small" : "muted small"}>{REQUIRED.has(label) ? "required" : excessive ? "not needed by CommitGuard" : ""}</span>
                </li>
              );
            })}
          </ul>
        </Panel>
      </div>
      <Panel title="Repositories" id="repositories" flush>
        <Repositories installationId={installation.id} />
      </Panel>
      {recent_events.length ? (
        <Panel title="Recent events" id="events" flush>
          <AuditTable events={recent_events} caption="Recent installation events" />
        </Panel>
      ) : null}
    </>
  );
}
