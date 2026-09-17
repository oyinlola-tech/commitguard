import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, SkipForward, Undo2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { advanceRollout, getRollout, pauseRollout, resumeRollout, rollbackRollout } from "../api/policyWorkflow";
import type { Rollout } from "../api/types";
import { useSession } from "../auth/session";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { RolloutProgress } from "../components/Rollouts";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { count, plural } from "../lib/format";
import { ROLLOUT_STATE, STAGE_STATE } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;
const IN_PROGRESS = ["pilot", "rollout", "paused"];

const percent = (value: number | undefined) => (value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`);

function targetLink(rollout: Rollout): string {
  if (rollout.target.type === "group") return routes.group(rollout.target.id);
  return routes.policy(rollout.organization_id);
}

function Status({ rollout }: { rollout: Rollout }) {
  if (rollout.state === "rolled_back") {
    return (
      <Notice tone="danger" title="Rolled back">
        Rolled back <Time value={rollout.rolled_back_at} />
        {rollout.rollback_version ? `: version v${rollout.rollback_version} restores the document of v${rollout.from_version}` : ""}. Every repository of the scope resolves the restored policy again.
      </Notice>
    );
  }
  if (rollout.state === "paused") {
    return (
      <Notice tone="warning" title="Paused">
        {rollout.paused_reason ?? "The rollout is paused."} Enrolled repositories keep v{rollout.to_version}; no further stage is enrolled until the rollout is resumed.
      </Notice>
    );
  }
  if (rollout.complete) {
    return (
      <Notice tone="success" title="Complete">
        Every repository in scope is enrolled and resolves v{rollout.to_version}
        {rollout.completed_at ? <> (<Time value={rollout.completed_at} />)</> : null}.
      </Notice>
    );
  }
  const unresolved = rollout.enrolled - rollout.propagated;
  return (
    <Notice title="Not complete">
      {plural(rollout.enrolled, "repository", "repositories")} of {count(rollout.scope_repositories)} enrolled
      {unresolved > 0 ? `; ${plural(unresolved, "enrolled repository has", "enrolled repositories have")} not resolved v${rollout.to_version} yet` : ""}. A rollout is complete only when every repository in scope is enrolled and resolves the new version.
    </Notice>
  );
}

export default function PolicyRollout() {
  const { rolloutId = "" } = useParams();
  const valid = HEX_ID.test(rolloutId);
  const queryClient = useQueryClient();
  const { organizations } = useSession();
  const query = useQuery({
    queryKey: ["governance", "rollout", rolloutId],
    queryFn: () => getRollout(rolloutId),
    enabled: valid,
    refetchInterval: (q) => (q.state.data && IN_PROGRESS.includes(q.state.data.state) ? 30_000 : false),
  });
  useDocumentTitle(query.data ? `Rollout ${query.data.target.label}` : "Rollout");
  const [dialog, setDialog] = useState<"advance" | "pause" | "rollback" | null>(null);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);
  const close = () => {
    setDialog(null);
    setReason("");
    setUnderstood(false);
  };
  const onDone = (rollout: Rollout) => {
    queryClient.setQueryData(["governance", "rollout", rolloutId], rollout);
    void queryClient.invalidateQueries({ queryKey: ["governance", rollout.organization_id] });
    close();
  };
  const advance = useMutation({ mutationFn: () => advanceRollout(rolloutId), onSuccess: onDone });
  const pause = useMutation({ mutationFn: () => pauseRollout(rolloutId, reason.trim()), onSuccess: onDone });
  const resume = useMutation({ mutationFn: () => resumeRollout(rolloutId), onSuccess: onDone });
  const rollback = useMutation({ mutationFn: () => rollbackRollout(rolloutId, reason.trim()), onSuccess: onDone });

  if (!valid) return <NotFoundContent resource="rollout" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading rollout…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="rollout" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this rollout." error={query.error} onRetry={() => void query.refetch()} />;

  const rollout = query.data;
  const access = organizations.find((o) => o.organization.id === rollout.organization_id);
  const canRollback = Boolean(access?.permissions.includes("policies:rollback")) && IN_PROGRESS.includes(rollout.state);
  const running = rollout.state === "pilot" || rollout.state === "rollout";
  const next = rollout.stages[rollout.current_stage + 1];
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.policyGovernance}>Policy governance</Link>}
        title={`Staged rollout: ${rollout.target.label}`}
        description={
          <>
            From v{rollout.from_version} to v{rollout.to_version} of the <Link to={targetLink(rollout)}>{rollout.target.label}</Link> policy · started by {rollout.created_by ?? "unknown"} <Time value={rollout.created_at} />
          </>
        }
        actions={
          <>
            {rollout.can_manage && running && next ? (
              <button type="button" className="button button--primary" onClick={() => setDialog("advance")}>
                <SkipForward size={14} aria-hidden="true" /> Advance to {next.name}
              </button>
            ) : null}
            {rollout.can_manage && running ? (
              <button type="button" className="button button--secondary" onClick={() => setDialog("pause")}>
                <Pause size={14} aria-hidden="true" /> Pause
              </button>
            ) : null}
            {rollout.can_manage && rollout.state === "paused" ? (
              <button type="button" className="button button--primary" onClick={() => resume.mutate()} disabled={resume.isPending}>
                <Play size={14} aria-hidden="true" /> {resume.isPending ? "Resuming…" : "Resume"}
              </button>
            ) : null}
            {canRollback ? (
              <button type="button" className="button button--danger-outline" onClick={() => setDialog("rollback")}>
                <Undo2 size={14} aria-hidden="true" /> Roll back
              </button>
            ) : null}
          </>
        }
      >
        <div className="badge-row">
          <Badge map={ROLLOUT_STATE} value={rollout.state} />
          {rollout.complete ? <span className="tag">Complete</span> : null}
        </div>
      </PageHeader>
      <ActionError error={resume.error} fallback="The rollout could not be resumed." />
      <Status rollout={rollout} />

      <div className="grid-2">
        <Panel title="Stages" id="stages">
          <ol className="stages">
            {rollout.stages.map((stage) => (
              <li key={stage.index} className={`stages__item stages__item--${stage.state}`} aria-current={stage.state === "current" ? "step" : undefined}>
                <span className="stages__marker" aria-hidden="true" />
                <div className="stages__body">
                  <p className="stages__title">
                    <span className="strong">{stage.name}</span> <Badge map={STAGE_STATE} value={stage.state} compact />
                  </p>
                  <p className="muted small">
                    {stage.kind === "percent" ? `${count(stage.percent ?? 0)}% of the scope` : `${plural(stage.repositories, "named repository", "named repositories")}`} · {plural(stage.enrolled, "repository", "repositories")} enrolled in this stage
                  </p>
                  {stage.state === "current" ? (
                    <p className="muted small">
                      Current since <Time value={rollout.stage_started_at} />
                    </p>
                  ) : null}
                </div>
              </li>
            ))}
          </ol>
        </Panel>
        <Panel title="Progress" id="progress">
          <RolloutProgress rollout={rollout} />
          <KeyValueList
            items={[
              ["Repositories in scope", count(rollout.scope_repositories)],
              ["Enrolled", count(rollout.enrolled)],
              ["Propagated", <>{count(rollout.propagated)} <span className="muted small">enrolled and resolving v{rollout.to_version}</span></>],
              ["Scans since enrollment", count(rollout.scanned)],
              ["Passed", count(rollout.passed)],
              ["Blocked", count(rollout.blocked)],
              ["Errors", count(rollout.errors)],
            ]}
          />
        </Panel>
      </div>

      <Panel title="Safety thresholds" id="thresholds">
        <KeyValueList
          items={[
            ["Pause above error rate", percent(rollout.thresholds.max_error_rate)],
            ["Pause above block rate", percent(rollout.thresholds.max_block_rate)],
            ["Scans before thresholds apply", rollout.thresholds.min_scans === undefined ? "—" : count(rollout.thresholds.min_scans)],
            ["Automatic pause", rollout.auto_pause ? "On" : "Off"],
            ["Automatic rollback", rollout.auto_rollback ? "On" : "Off"],
          ]}
        />
        <p className="muted small">Rates are shares of the completed scans of enrolled repositories in the current stage.</p>
      </Panel>

      <ConfirmDialog open={dialog === "advance"} tone="default" title={`Advance to ${next?.name ?? "the next stage"}`} confirmLabel="Advance" onCancel={close} onConfirm={() => advance.mutate()} busy={advance.isPending}>
        <p>
          The repositories of this stage are enrolled now and resolve v{rollout.to_version} on their next scan.{" "}
          {next?.kind === "percent" ? `The stage covers ${count(next.percent ?? 0)}% of the scope.` : ""}
        </p>
        <ActionError error={advance.error} fallback="The rollout could not be advanced." />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "pause"} tone="default" title="Pause the rollout" confirmLabel="Pause" onCancel={close} onConfirm={() => pause.mutate()} confirmDisabled={!reason.trim()} busy={pause.isPending}>
        <p>Enrolled repositories keep v{rollout.to_version}; no further stage is enrolled while the rollout is paused.</p>
        <div className="field">
          <label htmlFor="pause-rollout-reason">Reason (required, recorded in the audit log)</label>
          <textarea id="pause-rollout-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <ActionError error={pause.error} fallback="The rollout could not be paused." />
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "rollback"}
        title="Roll back the rollout"
        confirmLabel="Roll back"
        onCancel={close}
        onConfirm={() => rollback.mutate()}
        confirmDisabled={!reason.trim() || !understood}
        busy={rollback.isPending}
      >
        <p>
          A new version is published that restores the document of v{rollout.from_version}, and every repository of the scope resolves it again. Version history, audit events and notifications are kept.
        </p>
        <div className="field">
          <label htmlFor="rollback-rollout-reason">Reason (required, recorded in the audit log)</label>
          <textarea id="rollback-rollout-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <label className="checkbox">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> I understand this changes the effective policy of every repository in scope.
        </label>
        <ActionError error={rollback.error} fallback="The rollout could not be rolled back." />
      </ConfirmDialog>
    </>
  );
}
