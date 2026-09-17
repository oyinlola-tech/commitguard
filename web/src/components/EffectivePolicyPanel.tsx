import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePen, ShieldMinus, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ApiError } from "../api/client";
import { getEffectivePolicy, setRepositoryMode } from "../api/governance";
import { listGroups } from "../api/groups";
import type { EffectivePolicy, EffectivePolicyView, Permission, PolicyConflict, RepositoryMode, RuleProvenance } from "../api/types";
import { useSession } from "../auth/session";
import { count, plural } from "../lib/format";
import { POLICY_ACTION, POLICY_LEVEL_LABEL, PROPAGATION, REPOSITORY_MODE, STRENGTH_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";
import { ActionError } from "./ActionError";
import { Badge } from "./Badge";
import { ModeChoice } from "./BulkOperation";
import { ConfirmDialog } from "./ConfirmDialog";
import { KeyValueList, Notice, Time } from "./Primitives";
import { EmptyState, ErrorState, SkeletonRows } from "./States";

const upper = (action: string) => action.toUpperCase();

/** A conflict as the resolver recorded it: the requirement still applies, and the reason is the server's. */
export function ConflictNotice({ conflict, repository }: { conflict: PolicyConflict; repository: string }) {
  return (
    <section className="conflict" aria-label={`Policy conflict for ${conflict.policy_id}`}>
      <p className="conflict__title">
        Policy conflict · <code>{conflict.policy_id}</code>
      </p>
      <dl className="conflict__facts">
        <div><dt>Repository</dt><dd>{repository}</dd></div>
        <div><dt>Requested</dt><dd><strong>{conflict.requested_enabled ? upper(conflict.requested_action) : "DISABLED"}</strong> <span className="muted small">by {conflict.requested_label}</span></dd></div>
        <div><dt>{POLICY_LEVEL_LABEL[conflict.required_by] ?? conflict.required_by} requirement</dt><dd><strong>{upper(conflict.required_action)}</strong> <span className="muted small">{conflict.required_label}</span></dd></div>
        <div><dt>Effective</dt><dd><strong>{upper(conflict.effective_action)}</strong></dd></div>
        <div><dt>Reason</dt><dd>{conflict.reason}</dd></div>
      </dl>
    </section>
  );
}

function SourceCell({ rule }: { rule: RuleProvenance }) {
  return (
    <span className="stack">
      <span className="break">{rule.source_label}</span>
      <span className="muted small">{POLICY_LEVEL_LABEL[rule.source] ?? rule.source}</span>
    </span>
  );
}

function RulesTable({ effective, caption }: { effective: EffectivePolicy; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr><th scope="col">Rule</th><th scope="col">Action</th><th scope="col">Source</th><th scope="col">Strength</th><th scope="col">Required floor</th><th scope="col">Exception</th></tr>
        </thead>
        <tbody>
          {effective.rules.map((rule) => (
            <tr key={rule.policy_id}>
              <td data-label="Rule" className="table__primary"><Link to={routes.rule(rule.policy_id)}><code>{rule.policy_id}</code></Link></td>
              <td data-label="Action">
                <span className="stack">
                  {rule.enabled ? <Badge map={POLICY_ACTION} value={rule.action} compact /> : <span className="strong">DISABLED</span>}
                  {rule.monitor_mode ? <span className="muted small">Monitor mode reports BLOCK as WARN</span> : null}
                </span>
              </td>
              <td data-label="Source"><SourceCell rule={rule} /></td>
              <td data-label="Strength">{rule.enforcement ? STRENGTH_LABEL[rule.enforcement] : <span className="muted">—</span>}</td>
              <td data-label="Required floor">
                {rule.required_action ? (
                  <span className="stack">
                    <Badge map={POLICY_ACTION} value={rule.required_action} compact />
                    <span className="muted small break">{rule.required_label}</span>
                  </span>
                ) : (
                  <span className="muted">None</span>
                )}
              </td>
              <td data-label="Exception">
                {rule.exception_id ? (
                  <span className="stack">
                    <Link to={routes.exception(rule.exception_id)}>Exception</Link>
                    <span className="muted small">
                      {rule.action_before_exception ? `lowered from ${upper(rule.action_before_exception)} · ` : ""}
                      {rule.exception_expires_at ? <>expires <Time value={rule.exception_expires_at} /></> : "permanent"}
                    </span>
                  </span>
                ) : (
                  <span className="muted">None</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ModeDialog({ view, onClose }: { view: EffectivePolicyView; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<RepositoryMode>(view.mode === "enforce" ? "monitor" : "enforce");
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const change = useMutation({
    mutationFn: () => setRepositoryMode(view.organization_id, { repository_ids: [view.repository_id], mode, confirm: true, reason: reason.trim() || null }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["governance"] });
      void queryClient.invalidateQueries({ queryKey: ["repository", view.repository_id] });
      onClose();
    },
  });
  return (
    <ConfirmDialog
      open
      title={`Change the mode of ${view.full_name}`}
      confirmLabel={mode === "enforce" ? "Switch to enforce" : "Switch to monitor"}
      onCancel={onClose}
      onConfirm={() => change.mutate()}
      confirmDisabled={mode === view.mode || !understood || (mode === "monitor" && !reason.trim())}
      busy={change.isPending}
    >
      <p>
        Current mode: <Badge map={REPOSITORY_MODE} value={view.mode} compact />
      </p>
      <ModeChoice mode={mode} onChange={setMode} reason={reason} onReason={setReason} name="repository-mode" />
      <label className="checkbox">
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand how this changes enforcement for this repository.
      </label>
      <ActionError error={change.error} fallback="The mode could not be changed." />
    </ConfirmDialog>
  );
}

/**
 * The governed effective policy of one repository: every rule with its source,
 * strength, floor and exception, and the conflicts the resolver recorded.
 */
export function EffectivePolicyPanel({ repositoryId, canManage }: { repositoryId: number; canManage: boolean }) {
  const { organizations } = useSession();
  const query = useQuery({ queryKey: ["governance", "effective-policy", repositoryId], queryFn: () => getEffectivePolicy(repositoryId) });
  const organizationId = query.data?.view.organization_id;
  const groups = useQuery({ queryKey: ["governance", organizationId, "groups", false], queryFn: () => listGroups(organizationId ?? 0), enabled: organizationId !== undefined });
  const [changing, setChanging] = useState(false);

  if (query.isPending) return <SkeletonRows rows={4} label="Loading effective policy…" />;
  if (query.error instanceof ApiError && query.error.status === 404) {
    return <EmptyState title="No organization governance for this repository.">It is not part of an organization you can access.</EmptyState>;
  }
  if (query.error || !query.data) return <ErrorState title="We could not load the effective policy." error={query.error} onRetry={() => void query.refetch()} />;

  const { view, exceptions } = query.data;
  const access = organizations.find((o) => o.organization.id === view.organization_id);
  const can = (permission: Permission) => Boolean(access?.permissions.includes(permission));
  const conflicts = view.effective.rules.map((r) => r.conflict).filter((c): c is PolicyConflict => c !== null);
  const scanConflicts = view.last_scan_effective?.rules.map((r) => r.conflict).filter((c): c is PolicyConflict => c !== null) ?? [];
  const groupIds = Object.keys(view.versions.groups);
  const groupName = (id: string) => groups.data?.find((g) => g.id === id)?.name ?? "Group";
  return (
    <>
      <div className="effective__actions">
        {canManage ? (
          <button type="button" className="button button--secondary" onClick={() => setChanging(true)}>
            <SlidersHorizontal size={14} aria-hidden="true" /> Change mode
          </button>
        ) : null}
        {can("policies:write") ? (
          <Link className="button button--secondary" to={routes.newDraft({ type: "repository", id: view.repository_id })}>
            <FilePen size={14} aria-hidden="true" /> Propose repository policy change
          </Link>
        ) : null}
        {can("exceptions:create") ? (
          <Link className="button button--ghost" to={`${routes.newException}?scope=repository&id=${view.repository_id}`}>
            <ShieldMinus size={14} aria-hidden="true" /> Request an exception
          </Link>
        ) : null}
      </div>
      <KeyValueList
        items={[
          ["Mode", <><Badge map={REPOSITORY_MODE} value={view.mode} compact /> <span className="muted small">{view.mode === "monitor" ? "Violations are reported, not blocked." : "Blocks according to policy."}</span></>],
          ["Propagation", <><Badge map={PROPAGATION} value={view.propagation} compact /> <span className="muted small">resolved <Time value={view.resolved_at} /></span></>],
          [
            "Latest scan",
            view.last_scan_id ? (
              <>
                <Link to={routes.scan(view.last_scan_id)}>Scan</Link> <Time value={view.last_scan_completed_at} /> ·{" "}
                {view.last_scan_used_current_policy === true ? "used the current policy" : view.last_scan_used_current_policy === false ? <strong>used an earlier policy</strong> : "policy not recorded"}
              </>
            ) : (
              <span className="muted">No completed scan</span>
            ),
          ],
          [
            "Groups",
            groupIds.length ? (
              <span className="link-list">
                {groupIds.map((id) => (
                  <Link key={id} to={routes.group(id)}>{groupName(id)}{view.versions.groups[id] ? ` (policy v${view.versions.groups[id]})` : ""}</Link>
                ))}
              </span>
            ) : (
              <span className="muted">None</span>
            ),
          ],
          [
            "Policy versions",
            <>
              Organization {view.versions.organization_policy ? `v${view.versions.organization_policy}` : "none"} · repository policy {view.versions.repository_policy ? `v${view.versions.repository_policy}` : "none"}
              {view.versions.organization_rules ? ` · organization rules v${view.versions.organization_rules}` : ""}
            </>,
          ],
          [
            "Exceptions",
            exceptions ? (
              <Link to={`${routes.exceptions}?repository=${view.repository_id}`}>
                {count(exceptions.active)} active · {count(exceptions.expiring_soon)} expiring within 7 days
              </Link>
            ) : (
              "—"
            ),
          ],
        ]}
      />
      <p className="muted small">{view.effective.description}</p>
      <RulesTable effective={view.effective} caption={`Effective policy of ${view.full_name}`} />
      {conflicts.map((conflict) => (
        <ConflictNotice key={`current-${conflict.policy_id}`} conflict={conflict} repository={view.full_name} />
      ))}
      <h3 className="subheading">Conflicts at the latest scan</h3>
      <p className="muted small">
        The repository&apos;s own <code>.commitguard.yaml</code> is only known at scan time, so conflicts with it are taken from the latest completed scan.
      </p>
      {view.last_scan_effective === null ? (
        <p className="muted small">No completed scan has recorded its effective policy yet.</p>
      ) : scanConflicts.length ? (
        <>
          {scanConflicts.map((conflict) => (
            <ConflictNotice key={`scan-${conflict.policy_id}`} conflict={conflict} repository={view.full_name} />
          ))}
          <Notice tone="warning">{plural(scanConflicts.length, "rule was", "rules were")} requested weaker than required. The requirement applied.</Notice>
        </>
      ) : (
        <p className="small">No conflict: the repository configuration did not ask for less than any mandatory requirement.</p>
      )}
      {changing ? <ModeDialog view={view} onClose={() => setChanging(false)} /> : null}
    </>
  );
}
