import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { Link } from "react-router";

import { getTrends, reportUrl } from "../api/governance";
import type { ReportKind, TrendPoint } from "../api/types";
import { OrganizationGate } from "../components/OrganizationGate";
import { Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { useUrlState } from "../hooks/useUrlState";
import { count } from "../lib/format";
import { REPORT_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";

const PERIODS: [string, string][] = [
  ["7", "7 days"],
  ["30", "30 days"],
  ["90", "90 days"],
];

const HISTORY_COLUMNS: [string, string][] = [
  ["scans", "Scans"],
  ["blocked_scans", "Blocked scans"],
  ["scan_errors", "Scan errors"],
  ["new_violations", "New violations"],
  ["new_critical", "New critical"],
];

const SNAPSHOT_COLUMNS: [string, string][] = [
  ["protected_repositories", "Protected"],
  ["unprotected_repositories", "Unprotected"],
  ["open_violations", "Open violations"],
  ["critical_open", "Critical open"],
  ["active_exceptions", "Active exceptions"],
  ["drift_repositories", "Drift"],
];

const REPORTS: ReportKind[] = ["compliance", "coverage", "violations", "exceptions", "policy_changes", "installations"];

const DAY = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" });

/**
 * A table whose cells also draw a bar scaled to the column's largest value:
 * the numbers stay readable by screen readers, the bars are decoration.
 */
function BarTable({ caption, points, columns }: { caption: string; points: TrendPoint[]; columns: [string, string][] }) {
  const max = Object.fromEntries(columns.map(([key]) => [key, Math.max(0, ...points.map((p) => p.values[key] ?? 0))]));
  return (
    <div className="table-wrap">
      <table className="table bar-table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            {columns.map(([key, label]) => (
              <th key={key} scope="col">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.day}>
              <th scope="row" className="table__primary bar-table__day">
                <time dateTime={point.day}>{DAY.format(new Date(`${point.day}T00:00:00Z`))}</time>
              </th>
              {columns.map(([key, label]) => {
                const value = point.values[key];
                const share = value && max[key] ? Math.max(4, Math.round((value / (max[key] ?? 1)) * 100)) : 0;
                return (
                  <td key={key} data-label={label}>
                    <span className="bar-table__cell">
                      <span className="bar-table__value">{value === undefined ? "—" : count(value)}</span>
                      <span className="bar-table__track" aria-hidden="true">
                        <span className={`bar-table__bar bar-table__bar--${key}`} style={{ inlineSize: `${share}%` }} />
                      </span>
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Trends({ scope, days }: { scope: GovernanceScope; days: number }) {
  const query = useQuery({ queryKey: ["governance", scope.id, "trends", days], queryFn: () => getTrends(scope.id, days), placeholderData: (previous) => previous });
  return (
    <QueryBoundary query={query} errorTitle="We could not load trends." loading={<SkeletonRows rows={6} label="Loading trends…" />}>
      {(data) => (
        <>
          <Panel title="Scans and violations per day" id="history" flush>
            {data.history.length ? (
              <>
                <BarTable caption={`Scans and new violations per day, last ${data.days} days`} points={data.history} columns={HISTORY_COLUMNS} />
                <p className="panel__inset muted small">From recorded scans and violations. Days without a completed scan or a new violation are not listed. Computed <Time value={data.computed_at} />.</p>
              </>
            ) : (
              <EmptyState title="No scans or violations in this period." />
            )}
          </Panel>
          <Panel title="Daily security snapshots" id="snapshots" flush>
            <p className="panel__inset muted small">{data.snapshot_note}</p>
            {data.snapshots.length ? <BarTable caption={`Daily security snapshots, last ${data.days} days`} points={data.snapshots} columns={SNAPSHOT_COLUMNS} /> : <EmptyState title="No snapshot in this period." />}
          </Panel>
        </>
      )}
    </QueryBoundary>
  );
}

function Reports({ scope }: { scope: GovernanceScope }) {
  const kinds = REPORTS.filter((kind) => kind !== "policy_changes" || scope.has("audit:read"));
  return (
    <>
      <Notice tone="warning" title="Not a certification">
        Reports describe CommitGuard policy enforcement at the moment they are generated. They are not a SOC 2, ISO 27001 or any other certification or attestation.
      </Notice>
      <ul className="reports">
        {kinds.map((kind) => {
          const report = REPORT_LABEL[kind];
          return (
            <li key={kind} className="reports__item">
              <div>
                <p className="strong">{report?.label ?? kind}</p>
                <p className="muted small">{report?.description}</p>
              </div>
              <div className="reports__links">
                {(["csv", "json"] as const).map((format) => (
                  <a key={format} className="button button--secondary" href={reportUrl(scope.id, kind, format)} download>
                    <Download size={14} aria-hidden="true" /> {format.toUpperCase()}
                    <span className="visually-hidden"> — {report?.label ?? kind} report</span>
                  </a>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="muted small">Each export is a point-in-time snapshot of what your account can see, and is recorded in the audit log.</p>
    </>
  );
}

function Security({ scope }: { scope: GovernanceScope }) {
  const { values, update } = useUrlState(["days"] as const);
  const days = PERIODS.some(([key]) => key === values.days) ? Number(values.days) : 30;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.organization}>{scope.login}</Link>}
        title="Trends and reports"
        description="How scans, violations, protection and exceptions changed over time, and point-in-time reports to export."
        actions={
          <div className="segmented" role="group" aria-label="Period">
            {PERIODS.map(([key, label]) => (
              <button key={key} type="button" className={Number(key) === days ? "segmented__item segmented__item--active" : "segmented__item"} aria-pressed={Number(key) === days} onClick={() => update({ days: key })}>
                {label}
              </button>
            ))}
          </div>
        }
      />
      <div className="stack-lg">
        <Trends scope={scope} days={days} />
        <Panel title="Reports" id="reports">
          <Reports scope={scope} />
        </Panel>
      </div>
    </>
  );
}

export default function OrganizationSecurity() {
  useDocumentTitle("Trends and reports");
  return (
    <OrganizationGate title="Trends and reports" permission="security:read">
      {(scope) => <Security key={scope.id} scope={scope} />}
    </OrganizationGate>
  );
}
