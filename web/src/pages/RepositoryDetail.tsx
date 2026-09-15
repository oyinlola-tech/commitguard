import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { getRepository, refreshEnforcement, setMonitoring } from "../api/repositories";
import type { RepositoryDetail as Detail } from "../api/types";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { AuditTable } from "../components/AuditTable";
import { ExternalLink, KeyValueList, Notice, PageHeader, Panel, Sha, Time } from "../components/Primitives";
import { EmptyState, ErrorState, SkeletonRows } from "../components/States";
import { ScansTable, ViolationsTable } from "../components/Tables";
import { ReauthenticateNotice } from "../components/Reauthenticate";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { ACTIONS_STATUS, APP_CONNECTION, LOCAL_HOOKS, POLICY_ACTION, PROTECTION, REQUIRED_CHECK, SCAN_RESULT } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

function Enforcement({ detail }: { detail: Detail }) {
  const e = detail.enforcement;
  const rows: [string, React.ReactNode, string, string | null][] = [
    ["GitHub App", <Badge map={APP_CONNECTION} value={e.github_app.status} compact />, e.github_app.detail, null],
    ["GitHub Actions", <Badge map={ACTIONS_STATUS} value={e.github_actions.status} compact />, e.github_actions.detail, e.github_actions.checked_at],
    ["Required check", <Badge map={REQUIRED_CHECK} value={e.required_check.status} compact />, e.required_check.detail, e.required_check.checked_at],
    [
      "CommitGuard check",
      e.latest_check.result ? <Badge map={SCAN_RESULT} value={e.latest_check.result} compact /> : <span className="muted">No completed scan</span>,
      e.latest_check.head_sha ? `Latest completed scan of ${e.latest_check.head_sha.slice(0, 12)}` : "No scan has completed yet.",
      e.latest_check.completed_at,
    ],
    ["Local hooks", <Badge map={LOCAL_HOOKS} value={e.local_hooks.status} compact />, e.local_hooks.detail, null],
  ];
  return (
    <ul className="signals">
      {rows.map(([label, badge, text, checked]) => (
        <li key={label} className="signals__item">
          <span className="signals__label">{label}</span>
          <span className="signals__badge">{badge}</span>
          <span className="signals__detail">
            {text}
            {checked ? (
              <span className="muted">
                {" "}
                · checked <Time value={checked} />
              </span>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function RepositoryDetail() {
  const { repositoryId } = useParams();
  const id = Number(repositoryId);
  const valid = /^\d{1,16}$/.test(repositoryId ?? "") && id > 0;
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["repository", id], queryFn: () => getRepository(id), enabled: valid });
  useDocumentTitle(query.data?.repository.full_name ?? "Repository");
  const [pausing, setPausing] = useState(false);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);

  const refresh = useMutation({
    mutationFn: () => refreshEnforcement(id),
    onSuccess: (result) => queryClient.setQueryData(["repository", id], result.data),
  });
  const monitoring = useMutation({
    mutationFn: (enabled: boolean) => setMonitoring(id, enabled, enabled ? null : reason.trim(), !enabled),
    onSuccess: (result) => {
      queryClient.setQueryData(["repository", id], result.data);
      void queryClient.invalidateQueries({ queryKey: ["repositories"] });
      setPausing(false);
      setReason("");
      setUnderstood(false);
    },
  });

  if (!valid) return <NotFoundContent resource="repository" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading repository…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="repository" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this repository." error={query.error} onRetry={() => void query.refetch()} />;

  const detail = query.data;
  const repo = detail.repository;
  const mutationError = refresh.error ?? monitoring.error;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.repositories}>Repositories</Link>}
        title={repo.full_name}
        description={
          <>
            <ExternalLink href={repo.github_url}>View on GitHub</ExternalLink>
            {repo.default_branch ? <span className="muted"> · default branch {repo.default_branch}</span> : null}
          </>
        }
        actions={
          detail.permissions.manage ? (
            <>
              <button type="button" className="button button--secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
                <RefreshCw size={14} aria-hidden="true" className={refresh.isPending ? "spin" : undefined} /> {refresh.isPending ? "Checking GitHub…" : "Refresh enforcement status"}
              </button>
              {repo.monitoring_enabled ? (
                <button type="button" className="button button--danger-outline" onClick={() => setPausing(true)}>
                  <Pause size={14} aria-hidden="true" /> Pause monitoring
                </button>
              ) : (
                <button type="button" className="button button--primary" onClick={() => monitoring.mutate(true)} disabled={monitoring.isPending}>
                  <Play size={14} aria-hidden="true" /> Resume monitoring
                </button>
              )}
            </>
          ) : null
        }
      />
      {mutationError instanceof ApiError && mutationError.code === "REAUTHENTICATION_REQUIRED" ? <ReauthenticateNotice /> : null}
      {mutationError && !(mutationError instanceof ApiError && mutationError.code === "REAUTHENTICATION_REQUIRED") ? (
        <Notice tone="danger">{mutationError instanceof ApiError ? mutationError.message : "The change could not be saved."}</Notice>
      ) : null}
      <div className={`protection-banner protection-banner--${repo.protection}`}>
        <Badge map={PROTECTION} value={repo.protection} />
        <p>{repo.protection_reason}</p>
      </div>

      <div className="grid-2">
        <Panel title="Enforcement" id="enforcement">
          <Enforcement detail={detail} />
          <p className="muted small">CommitGuard never marks a repository protected without evidence from GitHub. It does not configure branch protection.</p>
        </Panel>
        <Panel title="Effective policy" id="policy" actions={detail.effective_policy_scan ? <Link to={routes.scan(detail.effective_policy_scan)}>From latest scan</Link> : null}>
          {detail.effective_policies.length ? (
            <>
              <p className="muted small">
                The policy that evaluated the latest completed scan
                {detail.organization_policy_version ? `, including organization policy v${detail.organization_policy_version}` : ""}.
              </p>
              <table className="table table--simple">
                <caption className="visually-hidden">Effective policy</caption>
                <thead>
                  <tr><th scope="col">Rule</th><th scope="col">Enabled</th><th scope="col">Action</th></tr>
                </thead>
                <tbody>
                  {detail.effective_policies.map((p) => (
                    <tr key={p.id}>
                      <td><Link to={routes.rule(p.id)}><code>{p.id}</code></Link></td>
                      <td>{p.enabled ? "Yes" : "No"}</td>
                      <td><Badge map={POLICY_ACTION} value={p.action} compact /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : (
            <EmptyState title="No completed scan yet.">The effective policy is recorded with each scan.</EmptyState>
          )}
        </Panel>
      </div>

      <Panel title="Details" id="details">
        <KeyValueList
          items={[
            ["Owner", repo.owner],
            ["Organization", repo.organization?.login ?? "—"],
            ["Monitoring", repo.monitoring_enabled ? "Enabled" : "Paused in CommitGuard"],
            ["Last scan", repo.last_scan ? <><Time value={repo.last_scan.created_at} /> · <Sha value={repo.last_scan.head_sha} /></> : "Never"],
            ["Open violations", String(repo.open_violations)],
            ["Open warnings", String(repo.open_warnings)],
          ]}
        />
      </Panel>

      <Panel title="Recent scans" id="scans" flush actions={<Link to={`${routes.scans}?repository=${repo.id}`}>All scans</Link>}>
        {detail.recent_scans.length ? <ScansTable scans={detail.recent_scans} caption="Recent scans" compact /> : <EmptyState title="No scans yet." />}
      </Panel>
      <Panel title="Open violations" id="violations" flush actions={<Link to={`${routes.violations}?repository=${repo.id}`}>All violations</Link>}>
        {detail.open_violations.length ? <ViolationsTable violations={detail.open_violations} caption="Open violations" compact /> : <EmptyState title="No open violations." />}
      </Panel>
      {detail.permissions.read_audit ? (
        <Panel title="Audit activity" id="audit" flush actions={<Link to={`${routes.audit}?repository=${repo.id}`}>Audit log</Link>}>
          {detail.audit.length ? <AuditTable events={detail.audit} caption="Recent audit activity" /> : <EmptyState title="No audit events yet." />}
        </Panel>
      ) : null}

      <ConfirmDialog
        open={pausing}
        title="Pause CommitGuard monitoring?"
        confirmLabel="Pause monitoring"
        onCancel={() => setPausing(false)}
        onConfirm={() => monitoring.mutate(false)}
        confirmDisabled={!understood || reason.trim().length === 0}
        busy={monitoring.isPending}
      >
        <p>This will stop CommitGuard monitoring <strong>{repo.full_name}</strong>: new pushes and pull requests are not scanned and no CommitGuard check is created.</p>
        <p>Existing historical scan data will remain unless your retention policy removes it. If branch protection requires the CommitGuard check, pull requests will wait for a check that never arrives.</p>
        <div className="field">
          <label htmlFor="pause-reason">Reason (recorded in the audit log)</label>
          <textarea id="pause-reason" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} rows={3} />
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand that commits to this repository will not be checked.
        </label>
      </ConfirmDialog>
    </>
  );
}
