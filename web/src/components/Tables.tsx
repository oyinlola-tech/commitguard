import type { RepositorySummary, ScanSummary, ViolationSummary } from "../api/types";
import { PROTECTION, SCAN_RESULT, SEVERITY, VIOLATION_STATUS, APP_CONNECTION } from "../lib/labels";
import { count, duration, scanEvent } from "../lib/format";
import { routes } from "../lib/routes";
import { Badge } from "./Badge";
import { DataTable, type Column } from "./DataTable";
import { Sha, Time } from "./Primitives";

export function ScansTable({ scans, caption = "Scans", compact = false }: { scans: ScanSummary[]; caption?: string; compact?: boolean }) {
  const columns: Column<ScanSummary>[] = [
    { key: "repository", header: "Repository", primary: true, cell: (s) => <span className="strong">{s.repository.full_name}</span> },
    { key: "event", header: "Event", cell: (s) => scanEvent(s) },
    { key: "commit", header: "Commit", cell: (s) => <Sha value={s.head_sha} /> },
    { key: "result", header: "Result", cell: (s) => <Badge map={SCAN_RESULT} value={s.result} compact /> },
    { key: "time", header: "Time", cell: (s) => <Time value={s.created_at} /> },
  ];
  if (!compact) {
    columns.splice(3, 0, { key: "commits", header: "Commits", align: "end", hideOnMobile: true, cell: (s) => count(s.commits_scanned) });
    columns.splice(4, 0, { key: "findings", header: "Findings", align: "end", cell: (s) => count(s.findings) });
    columns.push({ key: "duration", header: "Duration", align: "end", hideOnMobile: true, cell: (s) => duration(s.duration_ms) });
  }
  return <DataTable caption={caption} columns={columns} rows={scans} rowKey={(s) => s.id} rowHref={(s) => routes.scan(s.id)} />;
}

export function ViolationsTable({ violations, caption = "Violations", compact = false }: { violations: ViolationSummary[]; caption?: string; compact?: boolean }) {
  const columns: Column<ViolationSummary>[] = [
    { key: "rule", header: "Rule", primary: true, cell: (v) => <span className="stack"><span className="strong">{v.title}</span><code className="muted">{v.rule_id}</code></span> },
    { key: "severity", header: "Severity", cell: (v) => <Badge map={SEVERITY} value={v.severity} compact /> },
    { key: "repository", header: "Repository", cell: (v) => v.repository.full_name },
    { key: "commit", header: "Commit", cell: (v) => <Sha value={v.commit_sha} /> },
    { key: "status", header: "Status", cell: (v) => <Badge map={VIOLATION_STATUS} value={v.status} compact /> },
    { key: "detected", header: "Detected", cell: (v) => <Time value={v.last_detected_at} /> },
  ];
  if (!compact) columns.splice(4, 0, { key: "author", header: "Author", hideOnMobile: true, cell: (v) => <span className="truncate">{v.author ?? "—"}</span> });
  return <DataTable caption={caption} columns={columns} rows={violations} rowKey={(v) => v.id} rowHref={(v) => routes.violation(v.id)} />;
}

export function RepositoriesTable({ repositories, caption = "Repositories" }: { repositories: RepositorySummary[]; caption?: string }) {
  const columns: Column<RepositorySummary>[] = [
    { key: "repository", header: "Repository", primary: true, cell: (r) => <span className="stack"><span className="strong">{r.name}</span><span className="muted">{r.organization?.login ?? r.owner}</span></span> },
    { key: "protection", header: "Protection", cell: (r) => <Badge map={PROTECTION} value={r.protection} compact title={r.protection_reason} /> },
    { key: "last-scan", header: "Last scan", cell: (r) => <Time value={r.last_scan?.created_at} /> },
    { key: "result", header: "Last result", cell: (r) => (r.last_scan ? <Badge map={SCAN_RESULT} value={r.last_scan.result} compact /> : <span className="muted">—</span>) },
    { key: "violations", header: "Violations", align: "end", cell: (r) => <span className={r.critical_open ? "text-critical strong" : r.open_violations ? "strong" : "muted"}>{count(r.open_violations)}</span> },
    { key: "app", header: "GitHub App", hideOnMobile: true, cell: (r) => <Badge map={APP_CONNECTION} value={r.app_connection} compact /> },
  ];
  return <DataTable caption={caption} columns={columns} rows={repositories} rowKey={(r) => `${r.installation_id}-${r.id}`} rowHref={(r) => routes.repository(r.id)} />;
}
