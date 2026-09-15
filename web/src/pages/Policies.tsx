import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Lock } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "../api/client";
import { listPolicies, listPolicyVersions, previewPolicy, updatePolicy, type Floors } from "../api/policies";
import type { OrganizationPolicy, PolicyAction, PolicyChange } from "../api/types";
import { useSession } from "../auth/session";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { ReauthenticateNotice } from "../components/Reauthenticate";
import { EmptyState, QueryBoundary, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { POLICY_ACTION } from "../lib/labels";
import { routes } from "../lib/routes";

const FLOOR_LABEL = (value: PolicyAction | null) => (value === null ? "Repository decides" : value === "block" ? "Always BLOCK" : "At least WARN");

function floorsOf(policy: OrganizationPolicy): Floors {
  return Object.fromEntries(policy.rules.map((r) => [r.policy_id, r.organization_floor]));
}

function Versions({ organization }: { organization: number }) {
  const query = useQuery({ queryKey: ["policy-versions", organization], queryFn: () => listPolicyVersions(organization) });
  return (
    <QueryBoundary query={query} errorTitle="We could not load the version history." loading={<SkeletonRows rows={2} />} isEmpty={(p) => p.items.length === 0} empty={<p className="muted">No organization policy has been saved. Built-in defaults and each repository's trusted configuration apply.</p>}>
      {(page) => (
        <ol className="versions">
          {page.items.map((v) => (
            <li key={v.version} className="versions__item">
              <span className="versions__number">v{v.version}</span>
              <div>
                <p>
                  <span className="strong">{v.created_by.login ?? "unknown"}</span> · <Time value={v.created_at} />
                </p>
                <p className="muted small">
                  {Object.keys(v.floors).length ? Object.entries(v.floors).map(([id, action]) => `${id}: ${action.toUpperCase()}`).join(" · ") : "No floors"}
                  {v.reason ? ` — “${v.reason}”` : ""}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </QueryBoundary>
  );
}

function PolicyEditor({ policy }: { policy: OrganizationPolicy }) {
  const queryClient = useQueryClient();
  const original = useMemo(() => floorsOf(policy), [policy]);
  const [draft, setDraft] = useState<Floors>(original);
  const [pending, setPending] = useState<PolicyChange[] | null>(null);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
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
      setSaved(`Saved as version ${updated.version}.`);
    },
  });
  const preview = useMutation({
    mutationFn: () => previewPolicy(policy.organization.id, draft),
    onSuccess: (result) => {
      setSaved(null);
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
        {saved ? <Notice tone="success">{saved}</Notice> : null}
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
      <details className="disclosure panel__inset">
        <summary><History size={14} aria-hidden="true" /> Version history</summary>
        <Versions organization={policy.organization.id} />
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
  const { organization } = useSession();
  const query = useQuery({ queryKey: ["policies", organization], queryFn: () => listPolicies(organization) });
  return (
    <>
      <PageHeader title="Policies" description="Organization floors for each rule, and where the effective action comes from." />
      <QueryBoundary query={query} errorTitle="We could not load policies." isEmpty={(d) => d.length === 0} empty={<EmptyState title="No organization policies available to your role." />}>
        {(policies) => (
          <div className="stack-lg">
            {policies.map((policy) => (
              <PolicyEditor key={`${policy.organization.id}-${policy.version}`} policy={policy} />
            ))}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
