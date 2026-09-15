import { useQuery } from "@tanstack/react-query";
import { OctagonAlert, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { getOverview, type Period } from "../api/dashboard";
import { useSession } from "../auth/session";
import { Badge } from "../components/Badge";
import { Metric, Notice, PageHeader, Panel } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { RepositoriesTable, ScansTable, ViolationsTable } from "../components/Tables";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { count } from "../lib/format";
import { HEALTH, INTEGRATION } from "../lib/labels";
import { routes } from "../lib/routes";

const PERIODS: [Period, string][] = [
  ["24h", "Last 24 hours"],
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
];

export default function Overview() {
  useDocumentTitle("Overview");
  const { organization, organizations } = useSession();
  const [period, setPeriod] = useState<Period>("7d");
  const query = useQuery({
    queryKey: ["overview", period, organization],
    queryFn: () => getOverview(period, organization),
    staleTime: 15_000,
    refetchOnWindowFocus: true,
  });

  if (organizations.length === 0) {
    return (
      <>
        <PageHeader title="Overview" />
        <EmptyState title="You do not have access to an organization yet.">
          Ask an owner of your organization to grant you a role in CommitGuard. GitHub must also list the CommitGuard installation for your account.
        </EmptyState>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Overview"
        description="What CommitGuard is protecting, what it detected, and what is currently blocked."
        actions={
          <div className="segmented" role="group" aria-label="Period">
            {PERIODS.map(([key, label]) => (
              <button key={key} type="button" className={key === period ? "segmented__item segmented__item--active" : "segmented__item"} aria-pressed={key === period} onClick={() => setPeriod(key)}>
                {label}
              </button>
            ))}
          </div>
        }
      />
      <QueryBoundary query={query} errorTitle="We could not load the overview." loading={<SkeletonRows rows={8} label="Loading overview…" />}>
        {(data) => {
          const s = data.summary;
          const periodLabel = PERIODS.find(([key]) => key === data.period.key)?.[1] ?? "";
          return (
            <div className="stack-lg">
              {s.repositories_at_risk > 0 || data.integration.status !== "connected" ? (
                <Notice tone="danger" title={<><ShieldAlert size={16} aria-hidden="true" /> GitHub enforcement at risk</>}>
                  {data.integration.detail}
                  {s.repositories_at_risk > 0 ? ` ${count(s.repositories_at_risk)} repositor${s.repositories_at_risk === 1 ? "y is" : "ies are"} no longer checked by the GitHub App.` : ""}{" "}
                  <Link to={routes.installations}>Review GitHub installations</Link>
                </Notice>
              ) : null}
              {s.critical_open > 0 ? (
                <Notice tone="danger" title={<><OctagonAlert size={16} aria-hidden="true" /> CRITICAL: {count(s.critical_open)} open critical violation{s.critical_open === 1 ? "" : "s"}</>}>
                  <Link to={`${routes.violations}?severity=critical&status=open`}>Review critical violations</Link>
                </Notice>
              ) : null}
              <div className="metrics">
                <Metric label="Monitored repositories" value={count(s.repositories_monitored)} detail={`${count(s.repositories_protected)} verified protected · ${count(s.repositories_unknown)} unverified`} to={routes.repositories} />
                <Metric label={`Scans · ${periodLabel.toLowerCase()}`} value={count(s.scans)} detail={`${count(s.scans_passed)} passed · ${count(s.scans_blocked)} blocked · ${count(s.scans_error)} errors`} to={routes.scans} />
                <Metric label="Blocked scans" value={count(s.scans_blocked)} detail={periodLabel} to={`${routes.scans}?result=blocked`} tone={s.scans_blocked ? "danger" : undefined} />
                <Metric label="Open violations" value={count(s.open_violations)} detail={`${count(s.open_warnings)} open warnings`} to={`${routes.violations}?status=open`} tone={s.open_violations ? "danger" : undefined} />
                <Metric label="Critical" value={count(s.critical_open)} detail={`${count(s.high_open)} high severity open`} to={`${routes.violations}?severity=critical`} tone={s.critical_open ? "critical" : undefined} />
              </div>

              <div className="grid-2">
                <Panel title="Security health" id="health">
                  <ul className="health">
                    {data.health.map((check) => (
                      <li key={check.id} className="health__item">
                        <Badge map={HEALTH} value={check.status} compact />
                        <div>
                          <p className="strong">{check.label}</p>
                          <p className="muted">{check.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                  <details className="disclosure">
                    <summary>Why there is no score</summary>
                    <p>CommitGuard shows explicit checks instead of a percentage. Each check is computed from stored scans, violations, enforcement evidence and the GitHub installation, as documented in the dashboard guide. A repository counts as protected only when GitHub confirmed that a CommitGuard check is required.</p>
                  </details>
                </Panel>
                <Panel title="GitHub integration" id="integration" actions={<Link to={routes.installations}>Installations</Link>}>
                  <div className="integration">
                    <Badge map={INTEGRATION} value={data.integration.status} />
                    <p>{data.integration.detail}</p>
                    <p className="muted">
                      {count(data.integration.installations_connected)} connected · {count(data.integration.installations_suspended)} suspended · {count(data.integration.installations_disconnected)} disconnected
                    </p>
                  </div>
                </Panel>
              </div>

              <Panel title="Recent scans" id="recent-scans" flush actions={<Link to={routes.scans}>All scans</Link>}>
                {data.recent_scans.length ? <ScansTable scans={data.recent_scans} caption="Recent scans" compact /> : <EmptyState title="No scans yet.">Connect a GitHub repository to begin monitoring. Scans appear here after the next push or pull request.</EmptyState>}
              </Panel>
              <div className="grid-2">
                <Panel title="Recent violations" id="recent-violations" flush actions={<Link to={routes.violations}>All violations</Link>}>
                  {data.recent_violations.length ? <ViolationsTable violations={data.recent_violations} caption="Recent violations" compact /> : <EmptyState title="No violations detected." />}
                </Panel>
                <Panel title="Repository health" id="repository-health" flush actions={<Link to={routes.repositories}>All repositories</Link>}>
                  {data.repository_health.length ? <RepositoriesTable repositories={data.repository_health} caption="Repositories needing attention first" /> : <EmptyState title="No repositories yet." />}
                </Panel>
              </div>
            </div>
          );
        }}
      </QueryBoundary>
    </>
  );
}
