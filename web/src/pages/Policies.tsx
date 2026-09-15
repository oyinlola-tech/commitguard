import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompare, History, Lock, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { diffPolicy, listPolicies, listPolicyVersions, previewPolicy, rollbackPolicy, updatePolicy, type Floors } from "../api/policies";
import type { OrganizationPolicy, PolicyAction, PolicyChange, PolicyDiff, PolicyVersion } from "../api/types";
import { useSession } from "../auth/session";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { ReauthenticateNotice } from "../components/Reauthenticate";
import { Pagination, useCursorPager } from "../components/Pagination";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { POLICY_ACTION, POLICY_VERSION_STATUS } from "../lib/labels";
import { routes } from "../lib/routes";

const FLOOR_LABEL = (value: PolicyAction | null) => (value === null ? "Repository decides" : value === "block" ? "Always BLOCK" : "At least WARN");

function floorsOf(policy: OrganizationPolicy): Floors {
  return Object.fromEntries(policy.rules.map((r) => [r.policy_id, r.organization_floor]));
}

function DiffTable({ diff, caption }: { diff: PolicyDiff; caption: string }) {
  const rows = [
    ...diff.added.map((e) => ({ ...e, change: "Added" })),
    ...diff.changed.map((e) => ({ ...e, change: "Changed" })),
    ...diff.removed.map((e) => ({ ...e, change: "Removed" })),
  ];
  if (rows.length === 0) return <p className="muted small">No floor differences.</p>;
  return (
    <table className="table table--simple diff">
      <caption className="visually-hidden">{caption}</caption>
      <thead>
        <tr><th scope="col">Change</th><th scope="col">Rule</th><th scope="col">v{diff.from_version}</th><th scope="col">v{diff.to_version}</th></tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.policy_id} className={row.weakening ? "diff__row diff__row--weakening" : "diff__row"}>
            <td>{row.change}{row.weakening ? <span className="tag tag--danger">Weakens</span> : null}</td>
            <td><code>{row.policy_id}</code></td>
            <td>{FLOOR_LABEL(row.old)}</td>
            <td className="strong">{FLOOR_LABEL(row.new)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function VersionCompare({ organization, from, to }: { organization: number; from: number; to: number }) {
  const query = useQuery({ queryKey: ["policy-diff", organization, from, to], queryFn: () => diffPolicy(organization, from, to) });
  return (
    <QueryBoundary query={query} errorTitle="We could not compare these versions." loading={<SkeletonRows rows={2} />}>
      {(diff) => <DiffTable diff={diff} caption={`Differences from v${from} to v${to}`} />}
    </QueryBoundary>
  );
}

function RollbackDialog({
  policy,
  target,
  onClose,
}: {
  policy: OrganizationPolicy;
  target: PolicyVersion;
  onClose: (message?: string) => void;
}) {
  const queryClient = useQueryClient();
  const organization = policy.organization.id;
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const impact = useQuery({
    queryKey: ["policy-diff", organization, policy.version, target.version],
    queryFn: () => diffPolicy(organization, policy.version, target.version),
  });
  const rollback = useMutation({
    mutationFn: () =>
      rollbackPolicy(organization, { target_version: target.version, expected_current_version: policy.version, reason: reason.trim(), confirm: true }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["policies"] });
      void queryClient.invalidateQueries({ queryKey: ["policy-versions", organization] });
      onClose(`Rolled back: version ${result.newVersion} restores version ${result.restoredVersion}. Earlier versions are unchanged.`);
    },
  });
  const error = rollback.error;
  return (
    <ConfirmDialog
      open
      title="Roll back organization policy"
      confirmLabel={`Roll back to v${target.version}`}
      onCancel={() => onClose()}
      onConfirm={() => rollback.mutate()}
      confirmDisabled={!understood || reason.trim().length === 0 || impact.isPending}
      busy={rollback.isPending}
    >
      <dl className="kv kv--compact">
        <div className="kv__row"><dt>Current</dt><dd>v{policy.version}</dd></div>
        <div className="kv__row"><dt>Target</dt><dd>v{target.version} · {target.created_by.login ?? "unknown"} · <Time value={target.created_at} /></dd></div>
        <div className="kv__row"><dt>Result</dt><dd>A new version v{policy.version + 1} with the target's floors. No version is edited or deleted.</dd></div>
      </dl>
      <p className="strong">This will change the effective security policy for every repository in {policy.organization.login}.</p>
      <p className="muted small">Scans that already ran keep the version they were evaluated with; new scans use the restored policy. Existing violations are not resolved by a rollback.</p>
      {impact.data ? <DiffTable diff={impact.data} caption="Impact of the rollback" /> : <SkeletonRows rows={2} label="Loading impact…" />}
      {impact.data?.weakening ? (
        <Notice tone="warning" title="This rollback weakens enforcement">Commits blocked today may be allowed. A sign-in from the last 15 minutes is required.</Notice>
      ) : null}
      {error instanceof ApiError && error.code === "REAUTHENTICATION_REQUIRED" ? <ReauthenticateNotice /> : null}
      {error instanceof ApiError && error.code === "CONFLICT" ? (
        <Notice tone="warning" title="The policy changed">{error.message}</Notice>
      ) : null}
      {error && !(error instanceof ApiError && ["REAUTHENTICATION_REQUIRED", "CONFLICT"].includes(error.code)) ? (
        <Notice tone="danger">{error instanceof ApiError ? error.message : "The rollback failed. The active version is unchanged."}</Notice>
      ) : null}
      <div className="field">
        <label htmlFor="rollback-reason">Reason (required, recorded in the audit log and notification)</label>
        <textarea id="rollback-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand this changes the effective security policy.
      </label>
    </ConfirmDialog>
  );
}

function Versions({ policy, onMessage }: { policy: OrganizationPolicy; onMessage: (message: string) => void }) {
  const { can } = useSession();
  const organization = policy.organization.id;
  const [cursor, setCursor] = useState<string | null>(null);
  const pager = useCursorPager(String(organization), cursor, setCursor);
  const [comparing, setComparing] = useState<number | null>(null);
  const [target, setTarget] = useState<PolicyVersion | null>(null);
  const canRollback = can("policies:rollback", organization);
  const query = useQuery({ queryKey: ["policy-versions", organization, cursor], queryFn: () => listPolicyVersions(organization, cursor) });
  return (
    <>
      <QueryBoundary
        query={query}
        errorTitle="We could not load the version history."
        loading={<SkeletonRows rows={2} />}
        isEmpty={(p) => p.items.length === 0}
        empty={<p className="muted">No organization policy has been saved. Built-in defaults and each repository's trusted configuration apply.</p>}
      >
        {(page) => (
          <>
            <ol className="versions">
              {page.items.map((v) => (
                <li key={v.version} className={`versions__item versions__item--${v.status}`}>
                  <span className="versions__number">v{v.version}</span>
                  <div className="versions__body">
                    <p className="versions__title">
                      <Badge map={POLICY_VERSION_STATUS} value={v.status} compact />
                      {v.kind === "rollback" ? (
                        <span className="tag">Rollback · restores v{v.restored_version} · replaced v{v.rollback_of}</span>
                      ) : null}
                      <span className="muted small">
                        <span className="strong">{v.created_by.login ?? "unknown"}</span> · <Time value={v.created_at} />
                      </span>
                    </p>
                    <p className="small">{v.summary}</p>
                    {v.reason ? <p className="muted small">Reason: “{v.reason}”</p> : null}
                    {v.status === "archived" ? (
                      <div className="versions__actions">
                        <button
                          type="button"
                          className="button button--ghost"
                          aria-expanded={comparing === v.version}
                          onClick={() => setComparing(comparing === v.version ? null : v.version)}
                        >
                          <GitCompare size={14} aria-hidden="true" /> Compare with v{policy.version}
                        </button>
                        {canRollback ? (
                          <button type="button" className="button button--danger-outline" onClick={() => setTarget(v)}>
                            <Undo2 size={14} aria-hidden="true" /> Roll back to v{v.version}
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    {comparing === v.version ? <VersionCompare organization={organization} from={policy.version} to={v.version} /> : null}
                  </div>
                </li>
              ))}
            </ol>
            <Pagination page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={page.nextCursor} onPrevious={pager.previous} onNext={pager.next} label="Policy versions" />
          </>
        )}
      </QueryBoundary>
      {target ? (
        <RollbackDialog
          policy={policy}
          target={target}
          onClose={(done) => {
            setTarget(null);
            if (done) onMessage(done);
          }}
        />
      ) : null}
    </>
  );
}

function PolicyEditor({
  policy,
  historyOpen,
  message,
  onMessage,
}: {
  policy: OrganizationPolicy;
  historyOpen: boolean;
  message: string | null;
  onMessage: (message: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const original = useMemo(() => floorsOf(policy), [policy]);
  const [draft, setDraft] = useState<Floors>(original);
  const [pending, setPending] = useState<PolicyChange[] | null>(null);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [historyShown, setHistoryShown] = useState(historyOpen);
  const dirty = policy.rules.some((r) => (draft[r.policy_id] ?? null) !== r.organization_floor);

  const save = useMutation({
    mutationFn: (confirm: boolean) =>
      updatePolicy(policy.organization.id, { expected_version: policy.version, floors: draft, reason: reason.trim() || null, confirm_weakening: confirm }),
    onSuccess: ({ policy: updated }) => {
      queryClient.setQueryData(["policies", policy.organization.id], (old: OrganizationPolicy[] | undefined) => old?.map((p) => (p.organization.id === updated.organization.id ? updated : p)));
      void queryClient.invalidateQueries({ queryKey: ["policies"] });
      void queryClient.invalidateQueries({ queryKey: ["policy-versions", policy.organization.id] });
      setPending(null);
      setReason("");
      setUnderstood(false);
      // The editor remounts for the new version; the message lives on the page.
      onMessage(`Saved as version ${updated.version}.`);
    },
  });
  const preview = useMutation({
    mutationFn: () => previewPolicy(policy.organization.id, draft),
    onSuccess: (result) => {
      onMessage(null);
      if (result.weakening) setPending(result.changes);
      else save.mutate(false);
    },
  });
  const error = preview.error ?? save.error;
  const weakenings = pending?.filter((c) => c.weakening) ?? [];

  return (
    <Panel
      title={policy.organization.login}
      id={`policy-${policy.organization.id}`}
      actions={
        <span className="muted small">
          {policy.version ? <>Version {policy.version} · updated <Time value={policy.updated_at} /> by {policy.updated_by?.login ?? "unknown"}</> : "No organization policy yet"}
        </span>
      }
      flush
    >
      <div className="panel__inset">
        <p className="muted small">
          Effective policy = {policy.service_policy ? "service policy + " : ""}organization policy + the repository's trusted <code>.commitguard.yaml</code>. Organization floors can only make enforcement stricter: a repository cannot lower them.
        </p>
        {error instanceof ApiError && error.code === "REAUTHENTICATION_REQUIRED" ? <ReauthenticateNotice /> : null}
        {error instanceof ApiError && error.code === "CONFLICT" ? (
          <Notice tone="warning" title="Someone else changed this policy">
            {error.message}{" "}
            <button type="button" className="link-button" onClick={() => void queryClient.invalidateQueries({ queryKey: ["policies"] })}>
              Load the latest version
            </button>
          </Notice>
        ) : null}
        {error && !(error instanceof ApiError && ["REAUTHENTICATION_REQUIRED", "CONFLICT"].includes(error.code)) ? (
          <Notice tone="danger">{error instanceof ApiError ? error.message : "The policy could not be saved."}</Notice>
        ) : null}
        {message ? <Notice tone="success">{message}</Notice> : null}
      </div>
      <div className="table-wrap">
        <table className="table">
          <caption className="visually-hidden">Policy for {policy.organization.login}</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Built-in default</th>
              {policy.service_policy ? <th scope="col">Service floor</th> : null}
              <th scope="col">Organization floor</th>
              <th scope="col">Minimum</th>
              <th scope="col">Repository may</th>
            </tr>
          </thead>
          <tbody>
            {policy.rules.map((rule) => (
              <tr key={rule.policy_id}>
                <td data-label="Rule" className="table__primary">
                  <span className="stack">
                    <Link to={routes.rule(rule.policy_id)} className="strong">{rule.name}</Link>
                    <code className="muted">{rule.policy_id}</code>
                  </span>
                </td>
                <td data-label="Built-in default"><Badge map={POLICY_ACTION} value={rule.default_action} compact /></td>
                {policy.service_policy ? <td data-label="Service floor">{rule.service_floor ? <Badge map={POLICY_ACTION} value={rule.service_floor} compact /> : <span className="muted">—</span>}</td> : null}
                <td data-label="Organization floor">
                  {policy.can_write ? (
                    <>
                      <label className="visually-hidden" htmlFor={`floor-${policy.organization.id}-${rule.policy_id}`}>Organization floor for {rule.policy_id}</label>
                      <select
                        id={`floor-${policy.organization.id}-${rule.policy_id}`}
                        value={draft[rule.policy_id] ?? ""}
                        onChange={(e) => setDraft({ ...draft, [rule.policy_id]: (e.target.value || null) as PolicyAction | null })}
                      >
                        <option value="">Repository decides</option>
                        <option value="warn">At least WARN</option>
                        <option value="block">Always BLOCK</option>
                      </select>
                    </>
                  ) : (
                    FLOOR_LABEL(rule.organization_floor)
                  )}
                </td>
                <td data-label="Minimum">{rule.minimum_action ? <Badge map={POLICY_ACTION} value={rule.minimum_action} compact /> : <span className="muted">None</span>}</td>
                <td data-label="Repository may">{rule.repository_override === "any" ? "Set any action" : "Only tighten"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel__inset panel__footer">
        {policy.can_write ? (
          <>
            <div className="field field--grow">
              <label htmlFor={`reason-${policy.organization.id}`}>Reason for this change</label>
              <input id={`reason-${policy.organization.id}`} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Recorded in the audit log" />
            </div>
            <button type="button" className="button button--secondary" disabled={!dirty} onClick={() => setDraft(original)}>Discard</button>
            <button type="button" className="button button--primary" disabled={!dirty || preview.isPending || save.isPending} onClick={() => preview.mutate()}>
              {preview.isPending || save.isPending ? "Saving…" : "Save policy"}
            </button>
          </>
        ) : (
          <p className="muted small"><Lock size={12} aria-hidden="true" /> Your role can view this policy. Admins and owners can change it.</p>
        )}
      </div>
      <details
        className="disclosure panel__inset"
        open={historyShown || undefined}
        onToggle={(event) => setHistoryShown((event.currentTarget as HTMLDetailsElement).open)}
      >
        <summary><History size={14} aria-hidden="true" /> Version history and rollback</summary>
        {historyShown ? <Versions policy={policy} onMessage={(text) => onMessage(text)} /> : null}
      </details>

      <ConfirmDialog
        open={pending !== null}
        title="You are weakening a security enforcement rule"
        confirmLabel="Weaken enforcement"
        onCancel={() => setPending(null)}
        onConfirm={() => save.mutate(true)}
        confirmDisabled={!understood || reason.trim().length === 0}
        busy={save.isPending}
      >
        <p>This may allow commits that CommitGuard blocks today. Review each change:</p>
        <table className="table table--simple">
          <caption className="visually-hidden">Weakening changes</caption>
          <thead><tr><th scope="col">Rule</th><th scope="col">Current</th><th scope="col">New</th></tr></thead>
          <tbody>
            {weakenings.map((c) => (
              <tr key={c.policy_id}>
                <td><code>{c.policy_id}</code></td>
                <td>{FLOOR_LABEL(c.old)}</td>
                <td className="strong">{FLOOR_LABEL(c.new)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="field">
          <label htmlFor="weaken-reason">Reason (required, recorded in the audit log)</label>
          <textarea id="weaken-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand this may allow previously blocked commits.
        </label>
        <p className="muted small">Weakening requires a sign-in from the last 15 minutes.</p>
      </ConfirmDialog>
    </Panel>
  );
}

export default function Policies() {
  useDocumentTitle("Policies");
  const { organization: selected } = useSession();
  const { organizationId } = useParams();
  const linked = organizationId && /^\d{1,16}$/.test(organizationId) ? Number(organizationId) : null;
  const organization = linked ?? selected;
  const query = useQuery({ queryKey: ["policies", organization], queryFn: () => listPolicies(organization) });
  const [messages, setMessages] = useState<Record<number, string | null>>({});
  const [openHistory, setOpenHistory] = useState<Record<number, boolean>>({});
  return (
    <>
      <PageHeader title="Policies" description="Organization floors for each rule, and where the effective action comes from." />
      <QueryBoundary query={query} errorTitle="We could not load policies." isEmpty={(d) => d.length === 0} empty={<EmptyState title="No organization policies available to your role." />}>
        {(policies) => (
          <div className="stack-lg">
            {policies.map((policy) => (
              <PolicyEditor
                key={`${policy.organization.id}-${policy.version}`}
                policy={policy}
                historyOpen={linked === policy.organization.id || Boolean(openHistory[policy.organization.id])}
                message={messages[policy.organization.id] ?? null}
                onMessage={(text) => {
                  setMessages((current) => ({ ...current, [policy.organization.id]: text }));
                  if (text?.startsWith("Rolled back")) setOpenHistory((current) => ({ ...current, [policy.organization.id]: true }));
                }}
              />
            ))}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
