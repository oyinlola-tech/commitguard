import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Siren, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { listTargetVersions, rollbackTarget } from "../api/policyWorkflow";
import type { PolicyAction, PolicyChange, PolicyTargetType, PolicyVersion, ScopedRule } from "../api/types";
import { POLICY_ACTION, POLICY_VERSION_STATUS, STRENGTH_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";
import { ActionError } from "./ActionError";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { Pagination, useCursorPager } from "./Pagination";
import { Notice, Time } from "./Primitives";
import { QueryBoundary, SkeletonRows } from "./States";

/**
 * How a policy level treats one rule. A mandatory entry is a floor that
 * narrower levels cannot weaken; a default is a baseline they may replace;
 * no entry leaves the decision to narrower levels and the repository.
 */
export function Requirement({ mandatory, fallback }: { mandatory: PolicyAction | null; fallback: PolicyAction | null }) {
  if (mandatory) {
    return (
      <span className="requirement">
        <Badge map={POLICY_ACTION} value={mandatory} compact />
        <span className="requirement__strength">Mandatory</span>
      </span>
    );
  }
  if (fallback) {
    return (
      <span className="requirement">
        <Badge map={POLICY_ACTION} value={fallback} compact />
        <span className="requirement__strength">Default</span>
      </span>
    );
  }
  return <span className="muted">Repository decides</span>;
}

export function StrengthExplanation() {
  return (
    <dl className="kv kv--compact strength-help">
      <div className="kv__row">
        <dt>Mandatory</dt>
        <dd>A floor. Narrower levels and the repository&apos;s <code>.commitguard.yaml</code> may make the rule stricter, never weaker. Only an approved exception goes below it.</dd>
      </div>
      <div className="kv__row">
        <dt>Default</dt>
        <dd>A baseline. Groups, the repository policy and the repository&apos;s configuration may replace it in either direction.</dd>
      </div>
      <div className="kv__row">
        <dt>Repository decides</dt>
        <dd>This level says nothing about the rule: narrower levels, the repository configuration or the built-in default decide.</dd>
      </div>
    </dl>
  );
}

export function ScopedRulesTable({ rules, caption }: { rules: ScopedRule[]; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Rule</th>
            <th scope="col">Requirement</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr key={rule.policy_id}>
              <td data-label="Rule" className="table__primary">
                <span className="stack">
                  <Link to={routes.rule(rule.policy_id)} className="strong">{rule.name}</Link>
                  <code className="muted">{rule.policy_id}</code>
                </span>
              </td>
              <td data-label="Requirement">
                <Requirement mandatory={rule.mandatory} fallback={rule.default} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const actionText = (action: PolicyAction | null) => (action ? action.toUpperCase() : "Not set");

/** Changes as the server classified them; a weakening change is marked in text, not only colour. */
export function ChangesTable({ changes, caption, fromLabel = "Current", toLabel = "Proposed" }: { changes: PolicyChange[]; caption: string; fromLabel?: string; toLabel?: string }) {
  if (changes.length === 0) return <p className="muted small">No differences from the current version.</p>;
  return (
    <div className="table-wrap">
      <table className="table table--simple diff">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Rule</th>
            <th scope="col">Strength</th>
            <th scope="col">{fromLabel}</th>
            <th scope="col">{toLabel}</th>
            <th scope="col">Effect</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((change) => (
            <tr key={`${change.policy_id}-${change.enforcement}`} className={change.weakening ? "diff__row diff__row--weakening" : "diff__row"}>
              <td data-label="Rule"><code>{change.policy_id}</code></td>
              <td data-label="Strength">{STRENGTH_LABEL[change.enforcement] ?? change.enforcement}</td>
              <td data-label={fromLabel}>{actionText(change.old)}</td>
              <td data-label={toLabel} className="strong">{actionText(change.new)}</td>
              <td data-label="Effect">{change.weakening ? <span className="tag tag--danger">Weakens enforcement</span> : <span className="muted">Does not weaken</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TargetRollbackDialog({
  organization,
  type,
  targetId,
  current,
  target,
  onClose,
}: {
  organization: number;
  type: PolicyTargetType;
  targetId: string | null;
  current: number;
  target: PolicyVersion;
  onClose: (message?: string) => void;
}) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const rollback = useMutation({
    mutationFn: () => rollbackTarget(organization, type, targetId, { target_version: target.version, expected_current_version: current, reason: reason.trim(), confirm: true }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ["governance", organization] });
      onClose(`Rolled back: version ${result.version} restores version ${result.restored_version}. Earlier versions are unchanged.`);
    },
  });
  return (
    <ConfirmDialog
      open
      title="Roll back this policy"
      confirmLabel={`Roll back to v${target.version}`}
      onCancel={() => onClose()}
      onConfirm={() => rollback.mutate()}
      confirmDisabled={!understood || reason.trim().length === 0}
      busy={rollback.isPending}
    >
      <p>
        A new version v{current + 1} is published with the document of v{target.version}. No version is edited or deleted, and scans that already ran keep the version they used.
      </p>
      <ActionError error={rollback.error} fallback="The rollback failed. The active version is unchanged." />
      <div className="field">
        <label htmlFor="target-rollback-reason">Reason (required, recorded in the audit log)</label>
        <textarea id="target-rollback-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand this changes the effective policy of every repository this policy applies to.
      </label>
    </ConfirmDialog>
  );
}

/** Immutable versions of one policy target, newest first, with rollback for `policies:rollback`. */
export function TargetVersionHistory({
  organization,
  type,
  targetId,
  canRollback,
}: {
  organization: number;
  type: PolicyTargetType;
  targetId: string | null;
  canRollback: boolean;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const pager = useCursorPager(`${type}-${targetId}`, cursor, setCursor);
  const [target, setTarget] = useState<PolicyVersion | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["governance", organization, "target-versions", type, targetId, cursor],
    queryFn: () => listTargetVersions(organization, type, targetId, cursor),
  });
  const current = query.data?.policy.version ?? 0;
  return (
    <>
      {message ? <Notice tone="success">{message}</Notice> : null}
      <QueryBoundary
        query={query}
        errorTitle="We could not load the version history."
        loading={<SkeletonRows rows={2} />}
        isEmpty={(data) => data.versions.length === 0}
        empty={<p className="muted small">Nothing has been published for this target yet.</p>}
      >
        {(data) => (
          <>
            <ol className="versions">
              {data.versions.map((v) => (
                <li key={v.version} className={`versions__item versions__item--${v.status}`}>
                  <span className="versions__number">v{v.version}</span>
                  <div className="versions__body">
                    <p className="versions__title">
                      <Badge map={POLICY_VERSION_STATUS} value={v.status} compact />
                      {v.kind === "rollback" ? <span className="tag">Rollback · restores v{v.restored_version}</span> : null}
                      {v.emergency ? (
                        <span className="tag tag--danger">
                          <Siren size={11} aria-hidden="true" /> Emergency publication
                        </span>
                      ) : null}
                      <span className="muted small">
                        <span className="strong">{v.created_by.login ?? "unknown"}</span> · <Time value={v.created_at} />
                      </span>
                    </p>
                    <p className="small">{v.summary}</p>
                    {v.reason ? <p className="muted small">Reason: “{v.reason}”</p> : null}
                    <div className="versions__actions">
                      {v.draft_id ? <Link to={routes.draft(v.draft_id)} className="small">Reviewed draft</Link> : null}
                      {canRollback && v.status === "archived" ? (
                        <button type="button" className="button button--danger-outline" onClick={() => setTarget(v)}>
                          <Undo2 size={14} aria-hidden="true" /> Roll back to v{v.version}
                        </button>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
            <Pagination page={pager.page} hasPrevious={pager.hasPrevious} nextCursor={data.nextCursor} onPrevious={pager.previous} onNext={pager.next} label="Policy versions" />
          </>
        )}
      </QueryBoundary>
      {target ? (
        <TargetRollbackDialog
          organization={organization}
          type={type}
          targetId={targetId}
          current={current}
          target={target}
          onClose={(done) => {
            setTarget(null);
            if (done) setMessage(done);
          }}
        />
      ) : null}
    </>
  );
}
