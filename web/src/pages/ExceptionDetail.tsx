import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, CircleCheck, CircleDashed, CircleSlash, CircleX, Clock, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { ApiError } from "../api/client";
import { approveException, cancelException, getException, rejectException, revokeException } from "../api/exceptions";
import type { PolicyException } from "../api/types";
import { useSession } from "../auth/session";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { EXCEPTION_STATUS, POLICY_ACTION, SEVERITY, TARGET_TYPE_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;

interface Step {
  key: string;
  icon: ReactNode;
  title: string;
  at: string | null;
  detail?: ReactNode;
  current?: boolean;
}

/** The exception's lifecycle as recorded: requested -> decided -> active -> ended. */
function lifecycle(e: PolicyException): Step[] {
  const steps: Step[] = [
    {
      key: "requested",
      icon: <CircleDashed size={14} aria-hidden="true" />,
      title: `Requested by ${e.requested_by ?? "unknown"}`,
      at: e.requested_at,
      detail: e.requires_approval ? "Needs approval by someone else." : "No approval needed.",
      current: e.status === "requested",
    },
  ];
  if (e.status === "rejected") {
    steps.push({ key: "rejected", icon: <CircleX size={14} aria-hidden="true" />, title: `Rejected by ${e.decided_by ?? "unknown"}`, at: e.decided_at, detail: e.decision_note ? `“${e.decision_note}”` : undefined, current: true });
  } else if (e.status === "cancelled") {
    steps.push({ key: "cancelled", icon: <CircleSlash size={14} aria-hidden="true" />, title: `Cancelled by ${e.decided_by ?? "unknown"}`, at: e.decided_at, detail: e.decision_note ? `“${e.decision_note}”` : undefined, current: true });
  } else if (e.activated_at && e.decided_at) {
    steps.push({ key: "approved", icon: <Check size={14} aria-hidden="true" />, title: `Approved by ${e.decided_by ?? "unknown"}`, at: e.decided_at, detail: e.decision_note ? `“${e.decision_note}”` : undefined });
  }
  if (e.activated_at) {
    steps.push({ key: "active", icon: <CircleCheck size={14} aria-hidden="true" />, title: "Active", at: e.activated_at, detail: e.permanent ? "Permanent: no expiry." : <>Lowers {e.rule_id} to {e.action.toUpperCase()} until <Time value={e.expires_at} absolute /></>, current: e.status === "active" });
  }
  if (e.status === "revoked") {
    steps.push({ key: "revoked", icon: <Ban size={14} aria-hidden="true" />, title: `Revoked by ${e.revoked_by ?? "unknown"}`, at: e.revoked_at, detail: e.revoke_reason ? `“${e.revoke_reason}”` : undefined, current: true });
  }
  if (e.status === "expired") {
    steps.push({ key: "expired", icon: <Clock size={14} aria-hidden="true" />, title: "Expired", at: e.expired_at ?? e.expires_at, current: true });
  }
  return steps;
}

type Dialog = "approve" | "reject" | "revoke" | "cancel" | null;

export default function ExceptionDetail() {
  const { exceptionId = "" } = useParams();
  const valid = HEX_ID.test(exceptionId);
  const queryClient = useQueryClient();
  const { session } = useSession();
  const query = useQuery({ queryKey: ["governance", "exception", exceptionId], queryFn: () => getException(exceptionId), enabled: valid });
  useDocumentTitle(query.data ? `Exception ${query.data.rule_id}` : "Exception");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState("");
  const close = () => {
    setDialog(null);
    setNote("");
  };
  const onDone = (exception: PolicyException) => {
    queryClient.setQueryData(["governance", "exception", exceptionId], exception);
    void queryClient.invalidateQueries({ queryKey: ["governance", exception.organization_id] });
    close();
  };
  const approve = useMutation({ mutationFn: () => approveException(exceptionId, note.trim() || null), onSuccess: onDone });
  const reject = useMutation({ mutationFn: () => rejectException(exceptionId, note.trim()), onSuccess: onDone });
  const revoke = useMutation({ mutationFn: () => revokeException(exceptionId, note.trim()), onSuccess: onDone });
  const cancel = useMutation({ mutationFn: () => cancelException(exceptionId), onSuccess: onDone });

  if (!valid) return <NotFoundContent resource="exception" />;
  if (query.isPending) return <SkeletonRows rows={8} label="Loading exception…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="exception" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this exception." error={query.error} onRetry={() => void query.refetch()} />;

  const e = query.data;
  const scopeLink = e.scope.type === "group" ? routes.group(e.scope.id) : e.scope.type === "repository" ? routes.repository(Number(e.scope.id)) : routes.organization;
  const ownRequest = e.requested_by === session.user.login;
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.exceptions}>Exceptions</Link>}
        title={`${e.rule_name} exception`}
        description={
          <>
            {TARGET_TYPE_LABEL[e.scope.type]} <Link to={scopeLink}>{e.scope.label}</Link> · lowers <code>{e.rule_id}</code> to {e.action.toUpperCase()}
          </>
        }
        actions={
          <>
            {e.can_approve ? (
              <>
                <button type="button" className="button button--primary" onClick={() => setDialog("approve")}>
                  <Check size={14} aria-hidden="true" /> Approve
                </button>
                <button type="button" className="button button--danger-outline" onClick={() => setDialog("reject")}>
                  <CircleX size={14} aria-hidden="true" /> Reject
                </button>
              </>
            ) : null}
            {e.can_revoke ? (
              <button type="button" className="button button--danger-outline" onClick={() => setDialog("revoke")}>
                <Ban size={14} aria-hidden="true" /> Revoke
              </button>
            ) : null}
            {e.can_cancel ? (
              <button type="button" className="button button--secondary" onClick={() => setDialog("cancel")}>
                <X size={14} aria-hidden="true" /> Cancel request
              </button>
            ) : null}
          </>
        }
      >
        <div className="badge-row">
          <Badge map={EXCEPTION_STATUS} value={e.status} />
          <Badge map={SEVERITY} value={e.severity} />
          {e.expiring_soon && e.status === "active" ? <span className="tag tag--warning">Expiring soon</span> : null}
          {e.permanent ? <span className="tag tag--danger">Permanent</span> : null}
        </div>
      </PageHeader>
      {e.status === "requested" && e.requires_approval && ownRequest ? (
        <Notice tone="warning" title="Waiting for someone else">
          An exception must be approved by someone other than the requester. It has no effect until then.
        </Notice>
      ) : null}
      {e.status === "active" ? (
        <Notice tone="warning" title="In effect">
          {e.rule_id} is lowered to {e.action.toUpperCase()} for {e.scope.label}. Findings are still detected and recorded with this exception.
        </Notice>
      ) : null}

      <div className="grid-2">
        <Panel title="Details" id="details">
          <KeyValueList
            items={[
              ["Rule", <Link to={routes.rule(e.rule_id)}>{e.rule_name} <code className="muted">{e.rule_id}</code></Link>],
              ["Scope", <>{TARGET_TYPE_LABEL[e.scope.type]} · <Link to={scopeLink}>{e.scope.label}</Link></>],
              ["Lowered to", <Badge map={POLICY_ACTION} value={e.action} compact />],
              ["Justification", `“${e.reason}”`],
              ["Expires", e.permanent ? "Never (permanent)" : <Time value={e.expires_at} absolute />],
              ["Approval", e.requires_approval ? "Required" : "Not required"],
            ]}
          />
        </Panel>
        <Panel title="Lifecycle" id="lifecycle">
          <ol className="timeline lifecycle">
            {lifecycle(e).map((step) => (
              <li key={step.key} className={step.current ? "timeline__item lifecycle__item--current" : "timeline__item"}>
                {step.icon}
                <div>
                  <p className="strong">
                    {step.title}
                    {step.current ? <span className="visually-hidden"> (current state)</span> : null}
                  </p>
                  {step.at ? (
                    <p className="muted small">
                      <Time value={step.at} absolute />
                    </p>
                  ) : null}
                  {step.detail ? <p className="small">{step.detail}</p> : null}
                </div>
              </li>
            ))}
          </ol>
          <p className="muted small">Exceptions are never deleted: rejected, cancelled, revoked and expired exceptions stay in the history.</p>
        </Panel>
      </div>

      <ConfirmDialog open={dialog === "approve"} tone="default" title="Approve this exception" confirmLabel="Approve" onCancel={close} onConfirm={() => approve.mutate()} busy={approve.isPending}>
        <p>
          <code>{e.rule_id}</code> is lowered to {e.action.toUpperCase()} for {e.scope.label} as soon as you approve, {e.permanent ? "permanently" : <>until <Time value={e.expires_at} absolute /></>}.
        </p>
        <div className="field">
          <label htmlFor="approve-exception-note">Note (optional)</label>
          <textarea id="approve-exception-note" rows={2} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
        <ActionError error={approve.error} fallback="The approval could not be recorded." />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "reject"} title="Reject this exception" confirmLabel="Reject" onCancel={close} onConfirm={() => reject.mutate()} confirmDisabled={!note.trim()} busy={reject.isPending}>
        <div className="field">
          <label htmlFor="reject-exception-note">Reason (required, recorded and shown to the requester)</label>
          <textarea id="reject-exception-note" rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
        <ActionError error={reject.error} fallback="The rejection could not be recorded." />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "revoke"} title="Revoke this exception" confirmLabel="Revoke" onCancel={close} onConfirm={() => revoke.mutate()} confirmDisabled={!note.trim()} busy={revoke.isPending}>
        <p>
          <code>{e.rule_id}</code> is enforced again for {e.scope.label} as policy requires. Commits the exception allowed may now be blocked.
        </p>
        <div className="field">
          <label htmlFor="revoke-exception-reason">Reason (required, recorded in the audit log)</label>
          <textarea id="revoke-exception-reason" rows={3} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
        <ActionError error={revoke.error} fallback="The exception could not be revoked." />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "cancel"} tone="default" title="Cancel this request" confirmLabel="Cancel request" onCancel={close} onConfirm={() => cancel.mutate()} busy={cancel.isPending}>
        <p>The request is withdrawn and kept in the history.</p>
        <ActionError error={cancel.error} fallback="The request could not be cancelled." />
      </ConfirmDialog>
    </>
  );
}
