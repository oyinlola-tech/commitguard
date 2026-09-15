import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../api/client";
import { compareScan, getScan, listExecutions, requestRescan } from "../api/scans";
import type { ScanDetail as Detail } from "../api/types";
import { Badge } from "../components/Badge";
import { FindingCard } from "../components/FindingCard";
import { KeyValueList, Notice, PageHeader, Panel, Sha, Time } from "../components/Primitives";
import { EmptyState, ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { isInProgress, useBackoffPolling } from "../hooks/usePolling";
import { count, duration, scanEvent, shortSha } from "../lib/format";
import { MERGE_GROUP_STATE, POLICY_ACTION, SCAN_RESULT, TRIGGER_LABEL, statusStyle } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;

function Comparison({ scanId }: { scanId: string }) {
  const query = useQuery({ queryKey: ["scan-comparison", scanId], queryFn: () => compareScan(scanId) });
  if (!query.data) return null;
  const data = query.data;
  if (!data.previous_scan_id) return <p className="muted">This is the first completed scan of this pull request or branch.</p>;
  return (
    <div className="comparison">
      <p>
        Compared with the <Link to={routes.scan(data.previous_scan_id)}>previous completed scan</Link>:
      </p>
      <ul className="comparison__counts">
        <li><span className="strong">{count(data.new.length)}</span> new</li>
        <li><span className="strong">{count(data.resolved.length)}</span> no longer present</li>
        <li><span className="strong">{count(data.unchanged.length)}</span> unchanged</li>
      </ul>
    </div>
  );
}

function Executions({ scanId, current, polling }: { scanId: string; current: string; polling: number | false }) {
  const query = useQuery({ queryKey: ["scan-executions", scanId], queryFn: () => listExecutions(scanId), refetchInterval: polling });
  if (query.isPending) return <SkeletonRows rows={2} label="Loading executions…" />;
  if (query.error || !query.data) return <ErrorState title="We could not load the execution history." error={query.error} onRetry={() => void query.refetch()} />;
  const history = query.data;
  return (
    <>
      {history.policy_changed ? (
        <Notice tone="warning" title="Executions used different policy versions">
          Compare the policy version of each execution before comparing results. A historical result always keeps the policy it was evaluated with.
        </Notice>
      ) : null}
      {history.rules_changed ? <Notice tone="info">Executions used different rules versions.</Notice> : null}
      <div className="table-wrap">
        <table className="table">
          <caption className="visually-hidden">Executions of this scan</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Trigger</th>
              <th scope="col">Result</th>
              <th scope="col">Commit</th>
              <th scope="col">Policy</th>
              <th scope="col">Rules</th>
              <th scope="col">Started</th>
              <th scope="col">Duration</th>
            </tr>
          </thead>
          <tbody>
            {history.items.map((e) => (
              <tr key={e.id} aria-current={e.id === current ? "true" : undefined} className={e.id === current ? "table__row--current" : undefined}>
                <td data-label="#" className="table__primary">
                  {e.id === current ? <span className="strong">#{e.execution}</span> : <Link to={routes.scan(e.id)}>#{e.execution}</Link>}
                  {e.current ? <span className="tag">Latest</span> : null}
                  {e.id === current ? <span className="visually-hidden"> (this page)</span> : null}
                </td>
                <td data-label="Trigger">{TRIGGER_LABEL[e.trigger] ?? e.trigger}{e.requested_by ? <span className="muted"> · {e.requested_by}</span> : null}</td>
                <td data-label="Result"><Badge map={SCAN_RESULT} value={e.result} compact title={e.failure?.message} /></td>
                <td data-label="Commit"><Sha value={e.head_sha} /></td>
                <td data-label="Policy">{e.organization_policy_version ? `v${e.organization_policy_version}` : "—"}{e.policy_version ? <code className="muted" title={e.policy_version}> {e.policy_version.slice(0, 8)}</code> : null}</td>
                <td data-label="Rules">{e.rules_version ? <code title={e.rules_version}>{e.rules_version.slice(0, 8)}</code> : "—"}</td>
                <td data-label="Started"><Time value={e.started_at ?? e.created_at} /></td>
                <td data-label="Duration">{duration(e.duration_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">Re-runs, manual scans and automatic retries are new executions of the same commits and check. Earlier executions are never changed; the latest one determines the current GitHub check.</p>
    </>
  );
}

export default function ScanDetail() {
  const { scanId = "" } = useParams();
  const valid = HEX_ID.test(scanId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const refetchInterval = useBackoffPolling((data: Detail | undefined) => isInProgress(data?.scan.result));
  const query = useQuery({ queryKey: ["scan", scanId], queryFn: () => getScan(scanId), enabled: valid, refetchInterval });
  useDocumentTitle(query.data ? `Scan ${shortSha(query.data.scan.head_sha)}` : "Scan");
  const [announcement, setAnnouncement] = useState("");
  const lastResult = useRef<string | null>(null);

  useEffect(() => {
    const result = query.data?.scan.result;
    if (!result) return;
    if (lastResult.current && isInProgress(lastResult.current) && !isInProgress(result)) {
      setAnnouncement(`Scan completed: ${statusStyle(SCAN_RESULT, result).label}`);
    }
    lastResult.current = result;
  }, [query.data?.scan.result]);

  const rescan = useMutation({
    mutationFn: () => requestRescan(scanId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["scans"] });
      navigate(routes.scan(result.data.scan));
    },
  });

  if (!valid) return <NotFoundContent resource="scan" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading scan…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="scan" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this scan." error={query.error} onRetry={() => void query.refetch()} />;

  const detail = query.data;
  const scan = detail.scan;
  const violations = detail.findings.filter((f) => f.action === "block");
  const warnings = detail.findings.filter((f) => f.action === "warn");
  const allowed = detail.findings.filter((f) => f.action === "allow");
  const inProgress = isInProgress(scan.result);
  return (
    <>
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>
      <PageHeader
        eyebrow={<Link to={routes.scans}>Scans</Link>}
        title={
          <>
            Scan of <Sha value={scan.head_sha} length={12} />
          </>
        }
        description={`${scan.repository.full_name} · ${scanEvent(scan)}`}
        actions={
          detail.can_rescan ? (
            <button type="button" className="button button--secondary" onClick={() => rescan.mutate()} disabled={rescan.isPending}>
              <RotateCw size={14} aria-hidden="true" /> {rescan.isPending ? "Queueing…" : "Scan again"}
            </button>
          ) : null
        }
      />
      {rescan.error ? <Notice tone="danger">{rescan.error instanceof ApiError ? rescan.error.message : "The scan could not be queued."}</Notice> : null}

      <section className={`result-hero result-hero--${scan.result}`} aria-label="Scan result">
        <Badge map={SCAN_RESULT} value={scan.result} />
        <div className="result-hero__facts">
          {inProgress ? (
            <p>{scan.result === "queued" ? "Scan queued. This page updates automatically." : "Scan running. This page updates automatically."}</p>
          ) : detail.failure ? (
            <p>{scan.result === "cancelled" ? "The scan was cancelled: " : "The scan could not be completed: "}{detail.failure.message}</p>
          ) : (
            <p>
              {count(scan.commits_scanned)} commit{scan.commits_scanned === 1 ? "" : "s"} scanned · {count(violations.length)} violation{violations.length === 1 ? "" : "s"} · {count(warnings.length)} warning{warnings.length === 1 ? "" : "s"}
            </p>
          )}
          {scan.result === "error" ? <p className="muted">CommitGuard fails closed: the GitHub check for this commit reports a failure, not a success.</p> : null}
        </div>
      </section>

      <div className="grid-2">
        <Panel title="Scan" id="scan-details">
          <KeyValueList
            items={[
              ["Scan ID", <code className="break">{scan.scan_id ?? scan.id}</code>],
              ["Repository", <Link to={routes.repository(scan.repository.id)}>{scan.repository.full_name}</Link>],
              ["Event", scanEvent(scan)],
              ["Commit range", scan.base_sha ? <><Sha value={scan.base_sha} copy />{" .. "}<Sha value={scan.head_sha} copy /></> : <Sha value={scan.head_sha} copy />],
              ["Started", <Time value={scan.started_at} absolute />],
              ["Completed", <Time value={scan.completed_at} absolute />],
              ["Duration", duration(scan.duration_ms)],
              ["GitHub check", <><code>{scan.check_name}</code>{detail.conclusion ? ` · ${detail.conclusion}` : ""}</>],
              ["Trigger", `${TRIGGER_LABEL[scan.trigger] ?? scan.trigger} · execution #${scan.execution} of ${detail.executions}`],
              ["Requested by", scan.requested_by ?? "GitHub event"],
            ]}
          />
        </Panel>
        <Panel title="Reproducibility" id="reproducibility">
          <KeyValueList
            items={[
              ["Policy version", detail.organization_policy_version ? `Organization policy v${detail.organization_policy_version}` : "No organization policy"],
              ["Effective policy", detail.policy_version ? <code title={detail.policy_version}>{detail.policy_version.slice(0, 16)}</code> : "—"],
              ["Policy source", detail.policy_source ?? "—"],
              ["Rules version", detail.rules_version ? <code title={detail.rules_version}>{detail.rules_version.slice(0, 16)}</code> : "—"],
              ["CommitGuard version", detail.tool_version ?? "—"],
            ]}
          />
          {detail.effective_policies.length ? (
            <ul className="policy-chips" aria-label="Effective policies">
              {detail.effective_policies.map((p) => (
                <li key={p.id}>
                  <code>{p.id}</code> <Badge map={POLICY_ACTION} value={p.enabled ? p.action : "allow"} compact />
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      </div>

      {detail.merge_group ? (
        <Panel title="Merge queue" id="merge-group">
          <KeyValueList
            items={[
              ["Merge group", <><Badge map={MERGE_GROUP_STATE} value={detail.merge_group.state} compact />{detail.merge_group.destroyed_reason ? ` · ${detail.merge_group.destroyed_reason}` : ""}</>],
              ["Merge group commit", <Sha value={detail.merge_group.head_sha} copy />],
              ["Target branch", detail.merge_group.base_ref.replace(/^refs\/heads\//, "")],
              ["Queued pull requests", detail.merge_group.pull_requests.length ? detail.merge_group.pull_requests.map((n) => `#${n}`).join(", ") : "—"],
            ]}
          />
          <p className="muted small">
            This result belongs to the temporary merge group commit the merge queue tests (the pull request combined with the latest target branch and changes queued ahead of it), not to the pull request head.
            {scan.result === "blocked" || scan.result === "error" ? " Failure source: merge queue." : ""}
          </p>
        </Panel>
      ) : null}

      <Panel title={`Executions (${detail.executions})`} id="executions" flush>
        <Executions scanId={scan.id} current={scan.id} polling={inProgress ? 5000 : false} />
      </Panel>

      {detail.notices.length ? (
        <Panel title="Notices" id="notices">
          <ul className="notices">
            {detail.notices.map((notice, index) => (
              <li key={index}>{notice}</li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {!inProgress && !detail.failure ? (
        <Panel title="Statistics" id="statistics">
          <div className="metrics metrics--small">
            <div className="metric"><span className="metric__label">Commits scanned</span><span className="metric__value">{count(scan.commits_scanned)}</span></div>
            <div className="metric"><span className="metric__label">Findings</span><span className="metric__value">{count(scan.findings)}</span></div>
            <div className="metric"><span className="metric__label">Blocking</span><span className="metric__value">{count(violations.length)}</span></div>
            <div className="metric"><span className="metric__label">Warnings</span><span className="metric__value">{count(warnings.length)}</span></div>
            <div className="metric"><span className="metric__label">Detector failures</span><span className="metric__value">{count(detail.detector_failures)}</span></div>
          </div>
          <Comparison scanId={scan.id} />
        </Panel>
      ) : null}

      <Panel title={`Findings (${detail.findings.length})`} id="findings">
        {detail.findings.length === 0 ? (
          <EmptyState title={inProgress ? "Findings appear when the scan completes." : "No findings."} />
        ) : (
          <div className="stack">
            {[...violations, ...warnings, ...allowed].map((finding) => (
              <FindingCard key={finding.id} finding={finding} />
            ))}
          </div>
        )}
      </Panel>
      {!detail.can_rescan && detail.rescan_blocked_reason && !inProgress ? <p className="muted small">Scan again unavailable: {detail.rescan_blocked_reason}</p> : null}
    </>
  );
}
