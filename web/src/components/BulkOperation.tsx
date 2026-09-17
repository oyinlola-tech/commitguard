import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, RotateCcw, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";

import { cancelBulkOperation, createBulkOperation, getBulkOperation, idempotencyKey, retryBulkOperation } from "../api/bulk";
import { createGroup, listGroups } from "../api/groups";
import type { BulkOperation, BulkOperationType, RepositoryMode } from "../api/types";
import { useBackoffPolling } from "../hooks/usePolling";
import { count, plural } from "../lib/format";
import { BULK_ITEM_STATUS, BULK_STATUS, BULK_TYPE_LABEL, statusStyle } from "../lib/labels";
import { routes } from "../lib/routes";
import { ActionError } from "./ActionError";
import { Badge } from "./Badge";
import { ConfirmDialog } from "./ConfirmDialog";
import { Notice, Time } from "./Primitives";
import { ErrorState, SkeletonRows } from "./States";

/** The exact warnings shown before repositories change mode. */
export const ENFORCE_WARNING = "This change may cause GitHub checks to fail and prevent merges.";
export const MONITOR_WARNING = "Monitor mode stops CommitGuard from blocking: violations are recorded and alerted, but checks report them as warnings.";
export const PAUSE_WARNING = "Pausing monitoring stops CommitGuard checks for these repositories: new pushes and pull requests are not scanned.";

const TERMINAL = new Set(["completed", "partial", "failed", "cancelled"]);
export const isBulkInProgress = (operation: BulkOperation | undefined) => Boolean(operation && !TERMINAL.has(operation.status));

function describe(operation: BulkOperation): string {
  const label = BULK_TYPE_LABEL[operation.type] ?? operation.type;
  const mode = typeof operation.parameters.mode === "string" ? ` (${operation.parameters.mode})` : "";
  const enabled = operation.type === "set_monitoring" ? (operation.parameters.enabled ? " (resume)" : " (pause)") : "";
  return `${label}${mode}${enabled} · ${plural(operation.total, "repository", "repositories")}`;
}

/**
 * Progress of one bulk operation. The maintenance loop applies items in
 * batches in the background; this view polls until the server reports a final
 * status and never shows an operation as done while items are pending.
 */
export function BulkProgress({ operationId, onDismiss }: { operationId: string; onDismiss?: () => void }) {
  const queryClient = useQueryClient();
  const refetchInterval = useBackoffPolling(isBulkInProgress);
  const query = useQuery({ queryKey: ["governance", "bulk", operationId], queryFn: () => getBulkOperation(operationId), refetchInterval });
  const refresh = (data: BulkOperation) => {
    queryClient.setQueryData(["governance", "bulk", operationId], data);
    void queryClient.invalidateQueries({ queryKey: ["governance", data.organization_id] });
  };
  const cancel = useMutation({ mutationFn: () => cancelBulkOperation(operationId), onSuccess: refresh });
  const retry = useMutation({ mutationFn: () => retryBulkOperation(operationId), onSuccess: refresh });
  const running = useRef(false);
  const operation = query.data;
  useEffect(() => {
    // Membership, modes and posture changed: refresh the organization's views once when the operation ends.
    if (!operation) return;
    if (isBulkInProgress(operation)) running.current = true;
    else if (running.current) {
      running.current = false;
      void queryClient.invalidateQueries({ queryKey: ["governance", operation.organization_id] });
    }
  }, [operation, queryClient]);

  if (query.isPending) return <SkeletonRows rows={2} label="Loading bulk operation…" />;
  if (query.error || !operation) return <ErrorState title="We could not load this bulk operation." error={query.error} onRetry={() => void query.refetch()} />;

  const done = operation.completed + operation.failed + operation.skipped;
  const failures = operation.items.filter((item) => item.status === "failed");
  return (
    <section className="bulk-progress" aria-labelledby={`bulk-${operation.id}`}>
      <div className="bulk-progress__head">
        <h3 className="bulk-progress__title" id={`bulk-${operation.id}`}>
          {describe(operation)}
        </h3>
        <Badge map={BULK_STATUS} value={operation.status} compact />
        {onDismiss ? (
          <button type="button" className="icon-button icon-button--small bulk-progress__dismiss" onClick={onDismiss} aria-label="Hide this operation">
            <X size={12} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      <progress className="progress" max={Math.max(operation.total, 1)} value={done} aria-label={`${count(done)} of ${count(operation.total)} repositories processed`} />
      <p className="muted small" aria-live="polite">
        {statusStyle(BULK_STATUS, operation.status).label}: {count(operation.completed)} completed · {count(operation.failed)} failed · {count(operation.skipped)} skipped · {count(operation.pending)} pending
      </p>
      <p className="muted small">
        Requested by {operation.requested_by ?? "unknown"} <Time value={operation.created_at} />
        {operation.completed_at ? <> · finished <Time value={operation.completed_at} /></> : null}
        {operation.cancelled_by ? ` · cancelled by ${operation.cancelled_by}` : ""}
      </p>
      {isBulkInProgress(operation) ? (
        <p className="muted small">CommitGuard applies bulk operations in the background, in batches. This view checks the progress automatically.</p>
      ) : null}
      <ActionError error={cancel.error ?? retry.error} fallback="The operation could not be changed." />
      {failures.length ? (
        <div className="table-wrap">
          <table className="table table--simple">
            <caption className="visually-hidden">Repositories that failed</caption>
            <thead>
              <tr><th scope="col">Repository</th><th scope="col">Status</th><th scope="col">Reason</th><th scope="col">Attempts</th></tr>
            </thead>
            <tbody>
              {failures.map((item) => (
                <tr key={item.repository_id}>
                  <td data-label="Repository">{item.full_name ? <Link to={routes.repository(item.repository_id)}>{item.full_name}</Link> : <span className="muted">Repository {item.repository_id}</span>}</td>
                  <td data-label="Status"><Badge map={BULK_ITEM_STATUS} value={item.status} compact /></td>
                  <td data-label="Reason">{item.detail ?? "—"}</td>
                  <td data-label="Attempts">{count(item.attempts)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {operation.items.length > failures.length ? (
        <details className="disclosure">
          <summary>All {plural(operation.items.length, "repository", "repositories")}</summary>
          <ul className="plain-list bulk-progress__items">
            {operation.items.map((item) => (
              <li key={item.repository_id}>
                <Badge map={BULK_ITEM_STATUS} value={item.status} compact /> {item.full_name ?? `Repository ${item.repository_id}`}
                {item.detail ? <span className="muted small"> · {item.detail}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {operation.can_manage ? (
        <div className="versions__actions">
          {operation.failed > 0 && (operation.status === "partial" || operation.status === "failed") ? (
            <button type="button" className="button button--secondary" onClick={() => retry.mutate()} disabled={retry.isPending}>
              <RotateCcw size={14} aria-hidden="true" /> Retry {plural(operation.failed, "failed repository", "failed repositories")}
            </button>
          ) : null}
          {isBulkInProgress(operation) && operation.pending > 0 ? (
            <button type="button" className="button button--ghost" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
              <Ban size={14} aria-hidden="true" /> Cancel pending items
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** Choose an existing group or create one without leaving the dialog. */
export function GroupSelect({ organization, value, onChange, allowCreate }: { organization: number; value: string; onChange: (id: string) => void; allowCreate: boolean }) {
  const queryClient = useQueryClient();
  const id = useId();
  const groups = useQuery({ queryKey: ["governance", organization, "groups", false], queryFn: () => listGroups(organization) });
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => createGroup(organization, name.trim(), null),
    onSuccess: (group) => {
      void queryClient.invalidateQueries({ queryKey: ["governance", organization, "groups"] });
      queryClient.setQueryData(["governance", organization, "groups", false], (old: typeof groups.data) => (old ? [...old, group] : [group]));
      onChange(group.id);
      setName("");
    },
  });
  return (
    <div className="stack-sm">
      <div className="field">
        <label htmlFor={`${id}-group`}>Repository group</label>
        <select id={`${id}-group`} value={value} onChange={(e) => onChange(e.target.value)} disabled={groups.isPending}>
          <option value="">{groups.isPending ? "Loading groups…" : "Choose a group"}</option>
          {(groups.data ?? []).map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({plural(g.repository_count, "repository", "repositories")})
            </option>
          ))}
        </select>
      </div>
      {groups.error ? <ActionError error={groups.error} fallback="Groups could not be loaded." /> : null}
      {allowCreate ? (
        <div className="inline-fields">
          <div className="field field--grow">
            <label htmlFor={`${id}-new`}>Or create a group</label>
            <input id={`${id}-new`} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="Group name" />
          </div>
          <button type="button" className="button button--secondary" onClick={() => create.mutate()} disabled={!name.trim() || create.isPending}>
            {create.isPending ? "Creating…" : "Create group"}
          </button>
        </div>
      ) : null}
      <ActionError error={create.error} fallback="The group could not be created." />
    </div>
  );
}

export function ModeChoice({ mode, onChange, reason, onReason, name }: { mode: RepositoryMode; onChange: (mode: RepositoryMode) => void; reason: string; onReason: (reason: string) => void; name: string }) {
  const id = useId();
  return (
    <>
      <fieldset className="radio-group">
        <legend>Mode</legend>
        <label className="radio">
          <input type="radio" name={name} value="enforce" checked={mode === "enforce"} onChange={() => onChange("enforce")} />
          <span><span className="strong">Enforce</span> <span className="muted small">— block according to policy</span></span>
        </label>
        <label className="radio">
          <input type="radio" name={name} value="monitor" checked={mode === "monitor"} onChange={() => onChange("monitor")} />
          <span><span className="strong">Monitor</span> <span className="muted small">— scan, record and alert; checks report blocks as warnings</span></span>
        </label>
      </fieldset>
      {mode === "enforce" ? (
        <Notice tone="warning" title="Enforce mode">{ENFORCE_WARNING}</Notice>
      ) : (
        <>
          <Notice tone="warning" title="Monitor mode">{MONITOR_WARNING}</Notice>
          <div className="field">
            <label htmlFor={`${id}-reason`}>Reason (required to stop blocking, recorded in the audit log)</label>
            <textarea id={`${id}-reason`} rows={3} maxLength={500} value={reason} onChange={(e) => onReason(e.target.value)} />
          </div>
        </>
      )}
    </>
  );
}

export type BulkAction =
  | { type: "add_to_group" | "remove_from_group" }
  | { type: "onboard" | "set_mode" }
  | { type: "set_monitoring"; enabled: boolean }
  | { type: "schedule_scan" };

const DIALOG_TITLE: Record<BulkOperationType, string> = {
  add_to_group: "Add repositories to a group",
  remove_from_group: "Remove repositories from a group",
  onboard: "Onboard repositories",
  set_mode: "Change enforcement mode",
  set_monitoring: "Monitoring",
  schedule_scan: "Scan default branches",
};

/**
 * One bulk request. The idempotency key is created when the dialog opens, so a
 * repeated submission (double click, retry after a network error) returns the
 * operation the first one created instead of queuing a second one.
 */
export function BulkActionDialog({
  organization,
  action,
  repositories,
  onClose,
  onCreated,
}: {
  organization: number;
  action: BulkAction;
  repositories: Map<number, string>;
  onClose: () => void;
  onCreated: (operation: BulkOperation) => void;
}) {
  const [key] = useState(idempotencyKey);
  const [group, setGroup] = useState("");
  const [mode, setMode] = useState<RepositoryMode>("enforce");
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const ids = [...repositories.keys()];
  const pausing = action.type === "set_monitoring" && !action.enabled;
  const changesMode = action.type === "onboard" || action.type === "set_mode";
  const parameters = (): Record<string, unknown> => {
    if (action.type === "add_to_group" || action.type === "remove_from_group") return { group_id: group };
    if (changesMode) return { mode, reason: reason.trim() || null };
    if (action.type === "set_monitoring") return { enabled: action.enabled };
    return {};
  };
  const create = useMutation({
    mutationFn: () => createBulkOperation(organization, { type: action.type, repository_ids: ids, parameters: parameters(), idempotency_key: key, confirm: changesMode || pausing }),
    onSuccess: onCreated,
  });
  const needsGroup = action.type === "add_to_group" || action.type === "remove_from_group";
  const needsConfirmation = changesMode || pausing;
  const blocked = (needsGroup && !group) || (needsConfirmation && !understood) || (changesMode && mode === "monitor" && reason.trim().length === 0);
  const title = action.type === "set_monitoring" ? (action.enabled ? "Resume monitoring" : "Pause monitoring") : DIALOG_TITLE[action.type];
  const names = [...repositories.values()];
  return (
    <ConfirmDialog
      open
      title={title}
      tone={needsConfirmation ? "danger" : "default"}
      confirmLabel={`${title} (${plural(ids.length, "repository", "repositories")})`}
      onCancel={onClose}
      onConfirm={() => create.mutate()}
      confirmDisabled={blocked}
      busy={create.isPending}
    >
      <p>
        {plural(ids.length, "repository", "repositories")} selected:{" "}
        <span className="muted">
          {names.slice(0, 5).join(", ")}
          {names.length > 5 ? ` and ${count(names.length - 5)} more` : ""}
        </span>
      </p>
      {needsGroup ? <GroupSelect organization={organization} value={group} onChange={setGroup} allowCreate={action.type === "add_to_group"} /> : null}
      {needsGroup ? <p className="muted small">A group&apos;s policy applies to its repositories through normal resolution; changing membership changes their effective policy.</p> : null}
      {changesMode ? (
        <>
          {action.type === "onboard" ? <p>Onboarding confirms that CommitGuard governs these repositories in the mode you choose.</p> : null}
          <ModeChoice mode={mode} onChange={setMode} reason={reason} onReason={setReason} name={`bulk-mode-${key}`} />
        </>
      ) : null}
      {pausing ? <Notice tone="warning" title="Monitoring will stop">{PAUSE_WARNING} If branch protection requires the CommitGuard check, pull requests will wait for a check that never arrives.</Notice> : null}
      {action.type === "schedule_scan" ? <p>CommitGuard queues one scan of each repository&apos;s default branch with its current effective policy. Repositories that are archived, paused or already scanned at the current head are skipped.</p> : null}
      {action.type === "set_monitoring" && action.enabled ? <p>CommitGuard scans new pushes and pull requests of these repositories again.</p> : null}
      {needsConfirmation ? (
        <label className="checkbox">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand how this changes enforcement for these repositories.
        </label>
      ) : null}
      <ActionError error={create.error} fallback="The bulk operation could not be queued." />
      <p className="muted small">The operation runs in the background and is recorded in the audit log.</p>
    </ConfirmDialog>
  );
}
