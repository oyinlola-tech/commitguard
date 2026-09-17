import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, CircleX, FilePlus2, GitMerge, Save, Send, Siren, Upload, UserX } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { ApiError } from "../api/client";
import { getSettings, listRepositoryMatrix } from "../api/governance";
import { listGroups } from "../api/groups";
import {
  approveDraft,
  cancelDraft,
  createDraft,
  emergencyPublishDraft,
  getDraft,
  listTargetVersions,
  publishDraft,
  rebaseDraft,
  rejectDraft,
  submitDraft,
  updateDraft,
} from "../api/policyWorkflow";
import type { Permission, PolicyAction, PolicyDraft as Draft, PolicyTargetType, RolloutRequest } from "../api/types";
import { useSession } from "../auth/session";
import { ActionError } from "../components/ActionError";
import { Badge } from "../components/Badge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { OrganizationGate } from "../components/OrganizationGate";
import { ChangesTable, Requirement, StrengthExplanation } from "../components/PolicyDisplay";
import { KeyValueList, Notice, PageHeader, Panel, Time } from "../components/Primitives";
import { RepositoryPicker } from "../components/RepositoryPicker";
import {
  choicesFromDocument,
  currentChoices,
  documentFromChoices,
  RuleEditor,
  sameChoice,
  type EditableRule,
  type RuleChoices,
} from "../components/RuleEditor";
import { SimulationPanel } from "../components/Simulation";
import { ErrorState, SkeletonRows } from "../components/States";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import type { GovernanceScope } from "../hooks/useGovernanceOrganization";
import { plural } from "../lib/format";
import { APPROVAL_STATUS, DRAFT_STATE, TARGET_TYPE_LABEL } from "../lib/labels";
import { routes } from "../lib/routes";
import { NotFoundContent } from "./NotFound";

const HEX_ID = /^[0-9a-f]{32}$/;

interface Prefill {
  floors?: Record<string, PolicyAction>;
  defaults?: Record<string, PolicyAction>;
  title?: string;
  reason?: string | null;
}

const hasRules = (choices: RuleChoices) => Object.values(choices).some((c) => c.strength !== "none");

/* ------------------------------------------------------------------------ */
/* Creating a draft                                                         */
/* ------------------------------------------------------------------------ */

function TargetSelector({
  scope,
  type,
  targetId,
  onChange,
}: {
  scope: GovernanceScope;
  type: PolicyTargetType;
  targetId: string;
  onChange: (type: PolicyTargetType, targetId: string) => void;
}) {
  const [q, setQ] = useState("");
  const groups = useQuery({ queryKey: ["governance", scope.id, "groups", false], queryFn: () => listGroups(scope.id), enabled: type === "group" });
  const repositories = useQuery({
    queryKey: ["governance", scope.id, "repository-picker", q, { sort: "name" }],
    queryFn: () => listRepositoryMatrix(scope.id, { q: q || undefined, sort: "name", limit: 100 }),
    enabled: type === "repository",
    placeholderData: (previous) => previous,
  });
  return (
    <div className="stack-sm">
      <fieldset className="radio-group">
        <legend>Target</legend>
        {(["organization", "group", "repository"] as const).map((value) => (
          <label key={value} className="radio">
            <input type="radio" name="draft-target" checked={type === value} onChange={() => onChange(value, "")} />
            <span>
              <span className="strong">{TARGET_TYPE_LABEL[value]}</span>{" "}
              <span className="muted small">
                {value === "organization" ? "— every repository of the organization" : value === "group" ? "— the repositories of one group" : "— one repository"}
              </span>
            </span>
          </label>
        ))}
      </fieldset>
      {type === "group" ? (
        <div className="field">
          <label htmlFor="draft-group">Group</label>
          <select id="draft-group" value={targetId} onChange={(e) => onChange("group", e.target.value)} disabled={groups.isPending}>
            <option value="">{groups.isPending ? "Loading groups…" : "Choose a group"}</option>
            {(groups.data ?? []).map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
        </div>
      ) : null}
      {type === "repository" ? (
        <div className="inline-fields">
          <div className="field">
            <label htmlFor="draft-repository-search">Search repositories</label>
            <input id="draft-repository-search" type="search" maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} placeholder="owner/name" />
          </div>
          <div className="field field--grow">
            <label htmlFor="draft-repository">Repository</label>
            <select id="draft-repository" value={targetId} onChange={(e) => onChange("repository", e.target.value)} disabled={repositories.isPending}>
              <option value="">{repositories.isPending ? "Loading repositories…" : "Choose a repository"}</option>
              {(repositories.data?.items ?? []).map((r) => (
                <option key={r.repository_id} value={String(r.repository_id)}>{r.full_name}</option>
              ))}
            </select>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NewDraftForm({ scope, type, targetId, prefill }: { scope: GovernanceScope; type: PolicyTargetType; targetId: string; prefill: Prefill | null }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["governance", scope.id, "target-versions", type, type === "organization" ? null : targetId, null],
    queryFn: () => listTargetVersions(scope.id, type, type === "organization" ? null : targetId, null),
  });
  const base = useMemo(() => (current.data ? currentChoices(current.data.policy) : null), [current.data]);
  const [edited, setEdited] = useState<RuleChoices | null>(null);
  const [title, setTitle] = useState(prefill?.title ?? "");
  const [reason, setReason] = useState(prefill?.reason ?? "");
  const choices = edited ?? (base && prefill?.floors ? choicesFromDocument(base.rules, prefill.floors, prefill.defaults ?? {}) : base?.choices) ?? null;
  const create = useMutation({
    mutationFn: () => {
      const document = documentFromChoices(choices ?? {});
      return createDraft(scope.id, type, type === "organization" ? null : targetId, { ...document, title: title.trim(), reason: reason.trim() || null });
    },
    onSuccess: (draft) => {
      void queryClient.invalidateQueries({ queryKey: ["governance", scope.id] });
      navigate(routes.draft(draft.id));
    },
  });
  if (current.isPending) return <SkeletonRows rows={5} label="Loading the current policy…" />;
  if (current.error || !current.data || !base || !choices) return <ErrorState title="We could not load the current policy of this target." error={current.error} onRetry={() => void current.refetch()} />;
  const changed = base.rules.filter((r) => !sameChoice(choices[r.policy_id], base.choices[r.policy_id])).length;
  return (
    <>
      <p className="muted small">
        {current.data.policy.version ? `The current version is v${current.data.policy.version}. ` : "Nothing is published for this target yet. "}
        The editor starts from the current version. Nothing is enforced until the change is published.
      </p>
      <RuleEditor rules={base.rules} choices={choices} original={base.choices} onChange={setEdited} />
      <div className="field">
        <label htmlFor="draft-title">Title</label>
        <input id="draft-title" maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="For example: Block AI co-authors in production" />
      </div>
      <div className="field">
        <label htmlFor="draft-reason">Reason (recorded with the version; required to publish a change that weakens enforcement)</label>
        <textarea id="draft-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <p className="muted small" aria-live="polite">
        {plural(changed, "rule changed", "rules changed")} from the current version. CommitGuard compares the draft with the current version, and marks changes that weaken enforcement, when you save it.
      </p>
      {!hasRules(choices) ? <Notice tone="warning">A policy must set at least one rule to mandatory or default.</Notice> : null}
      <ActionError error={create.error} fallback="The draft could not be created." />
      <div className="wizard__actions">
        <button type="button" className="button button--primary" onClick={() => create.mutate()} disabled={create.isPending || !hasRules(choices) || changed === 0}>
          <Save size={14} aria-hidden="true" /> {create.isPending ? "Saving…" : "Save draft"}
        </button>
      </div>
    </>
  );
}

function NewDraft({ scope }: { scope: GovernanceScope }) {
  useDocumentTitle("New policy change");
  const [params] = useSearchParams();
  const location = useLocation();
  const prefill = (location.state as { prefill?: Prefill } | null)?.prefill ?? null;
  const initialType = (["organization", "group", "repository"] as const).find((t) => t === params.get("target")) ?? "organization";
  const initialId = initialType === "organization" ? "" : (params.get("id") ?? "").slice(0, 64);
  const [type, setType] = useState<PolicyTargetType>(initialType);
  const [targetId, setTargetId] = useState(initialId);
  const ready = type === "organization" || targetId !== "";
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.policyGovernance}>Policy governance</Link>}
        title="New policy change"
        description="Draft a change to one policy. It is reviewed, optionally simulated and approved, and only takes effect when it is published."
      />
      {prefill ? <Notice>This draft starts from an earlier draft whose target has changed since. Review each rule against the current version.</Notice> : null}
      <div className="grid-2 grid-2--wide-start">
        <Panel title="Proposed policy" id="editor">
          <TargetSelector
            scope={scope}
            type={type}
            targetId={targetId}
            onChange={(nextType, nextId) => {
              setType(nextType);
              setTargetId(nextId);
            }}
          />
          {ready ? <NewDraftForm key={`${type}-${targetId}`} scope={scope} type={type} targetId={targetId} prefill={prefill} /> : <p className="muted">Choose the target to load its current policy.</p>}
        </Panel>
        <Panel title="Mandatory or default" id="strengths">
          <StrengthExplanation />
          <p className="muted small">
            Precedence, from broadest to narrowest: built-in defaults, organization, repository groups, repository policy, the repository&apos;s <code>.commitguard.yaml</code>. Mandatory requirements of every level then apply as floors; an approved exception is the only way below them.
          </p>
        </Panel>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------ */
/* Reviewing a draft                                                        */
/* ------------------------------------------------------------------------ */

function DraftEditor({ draft, onSaved }: { draft: Draft; onSaved: (draft: Draft) => void }) {
  const queryClient = useQueryClient();
  const targetId = draft.target.type === "organization" ? null : draft.target.id;
  const current = useQuery({
    queryKey: ["governance", draft.organization_id, "target-versions", draft.target.type, targetId, null],
    queryFn: () => listTargetVersions(draft.organization_id, draft.target.type, targetId, null),
  });
  const rules: EditableRule[] | null = useMemo(() => (current.data ? currentChoices(current.data.policy).rules : null), [current.data]);
  const saved = useMemo(() => (rules ? choicesFromDocument(rules, draft.floors, draft.defaults) : null), [rules, draft.floors, draft.defaults]);
  const [edited, setEdited] = useState<RuleChoices | null>(null);
  const [title, setTitle] = useState(draft.title);
  const [reason, setReason] = useState(draft.reason ?? "");
  const choices = edited ?? saved;
  const save = useMutation({
    mutationFn: () => updateDraft(draft.id, draft.revision, { ...documentFromChoices(choices ?? {}), title: title.trim(), reason: reason.trim() }),
    onSuccess: (updated) => {
      setEdited(null);
      onSaved(updated);
    },
  });
  if (current.isPending) return <SkeletonRows rows={4} label="Loading rules…" />;
  if (current.error || !rules || !saved || !choices) return <ErrorState title="We could not load the rules of this target." error={current.error} onRetry={() => void current.refetch()} />;
  const dirty = rules.some((r) => !sameChoice(choices[r.policy_id], saved[r.policy_id])) || title.trim() !== draft.title || reason.trim() !== (draft.reason ?? "");
  return (
    <>
      {draft.state === "approved" ? <Notice tone="warning" title="Editing cancels the approval">Approval binds to this exact document. Saving a change returns the draft to DRAFT and it must be approved again.</Notice> : null}
      <RuleEditor rules={rules} choices={choices} original={saved} onChange={setEdited} />
      <div className="field">
        <label htmlFor="edit-draft-title">Title</label>
        <input id="edit-draft-title" maxLength={100} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="edit-draft-reason">Reason</label>
        <textarea id="edit-draft-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <ActionError
        error={save.error}
        fallback="The draft could not be saved."
        onReload={() => {
          setEdited(null);
          save.reset();
          void queryClient.invalidateQueries({ queryKey: ["governance", "draft", draft.id] });
        }}
      />
      <div className="wizard__actions">
        {dirty ? <span className="muted small">Unsaved changes. The diff below updates when you save.</span> : null}
        <button type="button" className="button button--secondary" onClick={() => { setEdited(null); setTitle(draft.title); setReason(draft.reason ?? ""); }} disabled={!dirty || save.isPending}>
          Discard
        </button>
        <button type="button" className="button button--primary" onClick={() => save.mutate()} disabled={!dirty || !hasRules(choices) || save.isPending}>
          <Save size={14} aria-hidden="true" /> {save.isPending ? "Saving…" : "Save changes"}
        </button>
      </div>
    </>
  );
}

function RolloutBuilder({ draft, value, onChange }: { draft: Draft; value: RolloutRequest | null; onChange: (value: RolloutRequest | null) => void }) {
  const settings = useQuery({ queryKey: ["governance", draft.organization_id, "settings"], queryFn: () => getSettings(draft.organization_id) });
  const [pilot, setPilot] = useState<Map<number, string>>(new Map());
  const [percents, setPercents] = useState("50, 100");
  const [errorRate, setErrorRate] = useState<string | null>(null);
  const [blockRate, setBlockRate] = useState<string | null>(null);
  const [minScans, setMinScans] = useState<string | null>(null);
  const [autoPause, setAutoPause] = useState<boolean | null>(null);
  const [autoRollback, setAutoRollback] = useState<boolean | null>(null);
  const defaults = settings.data?.settings;
  const enabled = value !== null;

  const build = (next: {
    pilot?: Map<number, string>;
    percents?: string;
    errorRate?: string | null;
    blockRate?: string | null;
    minScans?: string | null;
    autoPause?: boolean | null;
    autoRollback?: boolean | null;
  }): RolloutRequest => {
    const p = next.pilot ?? pilot;
    const stages: RolloutRequest["stages"] = [];
    if (p.size) stages.push({ name: "Pilot", repositories: [...p.keys()] });
    for (const raw of (next.percents ?? percents).split(",")) {
      const percent = Number(raw.trim());
      if (raw.trim() && Number.isInteger(percent)) stages.push({ percent });
    }
    const rate = (text: string | null | undefined) => (text === null || text === undefined || text === "" ? undefined : Number(text) / 100);
    const thresholds = {
      max_error_rate: rate(next.errorRate !== undefined ? next.errorRate : errorRate),
      max_block_rate: rate(next.blockRate !== undefined ? next.blockRate : blockRate),
      min_scans: (() => {
        const text = next.minScans !== undefined ? next.minScans : minScans;
        return text === null || text === "" ? undefined : Number(text);
      })(),
    };
    const pause = next.autoPause !== undefined ? next.autoPause : autoPause;
    const rollback = next.autoRollback !== undefined ? next.autoRollback : autoRollback;
    return {
      stages,
      thresholds: Object.fromEntries(Object.entries(thresholds).filter(([, v]) => v !== undefined)),
      ...(pause !== null ? { auto_pause: pause } : {}),
      ...(rollback !== null ? { auto_rollback: rollback } : {}),
    };
  };
  const update = (next: Parameters<typeof build>[0]) => onChange(build(next));
  const percentText = (rate: number | undefined) => (rate === undefined ? "" : String(Math.round(rate * 1000) / 10));

  return (
    <div className="rollout-builder">
      <label className="checkbox">
        <input type="checkbox" checked={enabled} onChange={(e) => onChange(e.target.checked ? build({}) : null)} /> Roll out in stages
      </label>
      {enabled ? (
        <>
          <p className="muted small">
            Enrolled repositories resolve the new version; the others keep v{draft.current_version} until their stage. Stages advance when you choose to, and a rollout pauses when scans exceed the thresholds.
          </p>
          <h3 className="subheading">Pilot repositories (optional first stage)</h3>
          <RepositoryPicker
            organization={draft.organization_id}
            legend="Pilot repositories"
            selected={pilot}
            onChange={(next) => {
              setPilot(next);
              update({ pilot: next });
            }}
            filters={draft.target.type === "group" ? { group: draft.target.id } : {}}
          />
          <div className="field">
            <label htmlFor="rollout-percents">Percentage stages, in order (the last stage becomes 100%)</label>
            <input
              id="rollout-percents"
              inputMode="numeric"
              value={percents}
              onChange={(e) => {
                setPercents(e.target.value);
                update({ percents: e.target.value });
              }}
              placeholder="25, 50, 100"
            />
          </div>
          <div className="inline-fields">
            <div className="field">
              <label htmlFor="rollout-error-rate">Pause above error rate (%)</label>
              <input id="rollout-error-rate" type="number" min={0} max={100} step={1} value={errorRate ?? percentText(defaults?.rollout_max_error_rate)} onChange={(e) => { setErrorRate(e.target.value); update({ errorRate: e.target.value }); }} />
            </div>
            <div className="field">
              <label htmlFor="rollout-block-rate">Pause above block rate (%)</label>
              <input id="rollout-block-rate" type="number" min={0} max={100} step={1} value={blockRate ?? percentText(defaults?.rollout_max_block_rate)} onChange={(e) => { setBlockRate(e.target.value); update({ blockRate: e.target.value }); }} />
            </div>
            <div className="field">
              <label htmlFor="rollout-min-scans">Scans before thresholds apply</label>
              <input id="rollout-min-scans" type="number" min={1} step={1} value={minScans ?? (defaults ? String(defaults.rollout_min_scans) : "")} onChange={(e) => { setMinScans(e.target.value); update({ minScans: e.target.value }); }} />
            </div>
          </div>
          <label className="checkbox">
            <input type="checkbox" checked={autoPause ?? defaults?.rollout_auto_pause ?? true} onChange={(e) => { setAutoPause(e.target.checked); update({ autoPause: e.target.checked }); }} /> Pause automatically when a threshold is exceeded
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={autoRollback ?? defaults?.rollout_auto_rollback ?? false} onChange={(e) => { setAutoRollback(e.target.checked); update({ autoRollback: e.target.checked }); }} /> Roll back automatically (publishes a new version restoring v{draft.current_version})
          </label>
          {value && value.stages.length === 0 ? <Notice tone="warning">Choose pilot repositories or enter at least one percentage.</Notice> : null}
        </>
      ) : null}
    </div>
  );
}

function PublishDialog({ draft, onClose, onDone }: { draft: Draft; onClose: () => void; onDone: (draft: Draft) => void }) {
  const [confirmed, setConfirmed] = useState(false);
  const [rollout, setRollout] = useState<RolloutRequest | null>(null);
  const [reason, setReason] = useState("");
  const publish = useMutation({ mutationFn: () => publishDraft(draft.id, draft.weakening && confirmed, rollout, reason.trim() || null), onSuccess: onDone });
  const stageable = draft.target.type !== "repository";
  // A weakening publication needs a reason: the draft's, or one given here.
  const reasonRequired = draft.weakening && !draft.reason;
  return (
    <ConfirmDialog
      open
      title={draft.weakening ? "Publish a change that weakens enforcement" : "Publish this policy change"}
      tone={draft.weakening ? "danger" : "default"}
      confirmLabel={`Publish v${draft.current_version + 1}`}
      onCancel={onClose}
      onConfirm={() => publish.mutate()}
      confirmDisabled={(draft.weakening && !confirmed) || (reasonRequired && !reason.trim()) || draft.rebase_required || (rollout !== null && rollout.stages.length === 0)}
      busy={publish.isPending}
    >
      <p>
        Publishing creates version v{draft.current_version + 1} of the {draft.target.label} policy. Versions are immutable; scans that already ran keep the version they used.
      </p>
      <ChangesTable changes={draft.changes} caption="Changes to publish" />
      {draft.weakening ? (
        <>
          <Notice tone="warning" title="This change weakens enforcement">
            Commits that CommitGuard blocks today may be allowed. A sign-in from the last 15 minutes is required, and administrators are notified.
          </Notice>
          <label className="checkbox">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm publishing a change that weakens enforcement.
          </label>
        </>
      ) : null}
      <div className="field">
        <label htmlFor="publish-reason">
          {reasonRequired
            ? "Reason (required: this change weakens enforcement; recorded with the version)"
            : draft.reason
              ? "Reason (optional; replaces the draft's reason on the version)"
              : "Reason (optional, recorded with the version)"}
        </label>
        <textarea id="publish-reason" rows={2} maxLength={500} value={reason} placeholder={draft.reason ?? undefined} onChange={(e) => setReason(e.target.value)} />
      </div>
      {stageable ? <RolloutBuilder draft={draft} value={rollout} onChange={setRollout} /> : <p className="muted small">A repository policy applies to one repository, so it is published without stages.</p>}
      <ActionError error={publish.error} fallback="The draft could not be published. The current version is unchanged." />
    </ConfirmDialog>
  );
}

function EmergencyDialog({ draft, onClose, onDone }: { draft: Draft; onClose: () => void; onDone: (draft: Draft) => void }) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [understood, setUnderstood] = useState(false);
  const publish = useMutation({ mutationFn: () => emergencyPublishDraft(draft.id, reason.trim(), draft.weakening && confirmed), onSuccess: onDone });
  return (
    <ConfirmDialog
      open
      title="Emergency publication"
      confirmLabel="Publish without approval"
      onCancel={onClose}
      onConfirm={() => publish.mutate()}
      confirmDisabled={!reason.trim() || !understood || (draft.weakening && !confirmed)}
      busy={publish.isPending}
    >
      <p>
        Emergency publication skips the approval workflow and publishes v{draft.current_version + 1} of the {draft.target.label} policy now.
      </p>
      <Notice tone="warning" title="It is never silent">
        The version is marked as an emergency publication, a critical audit event is recorded and administrators are notified, with your reason.
      </Notice>
      <ChangesTable changes={draft.changes} caption="Changes to publish" />
      {draft.weakening ? (
        <label className="checkbox">
          <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm publishing a change that weakens enforcement.
        </label>
      ) : null}
      <div className="field">
        <label htmlFor="emergency-reason">Reason (required, recorded and notified)</label>
        <textarea id="emergency-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      <label className="checkbox">
        <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} /> This is an emergency that cannot wait for approval.
      </label>
      <ActionError error={publish.error} fallback="The draft could not be published. The current version is unchanged." />
    </ConfirmDialog>
  );
}

type Dialog = "approve" | "reject" | "cancel" | "publish" | "emergency" | null;

function Workflow({ draft, can, onChange }: { draft: Draft; can: (permission: Permission) => boolean; onChange: (draft: Draft) => void }) {
  const { session } = useSession();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [reason, setReason] = useState("");
  const close = () => {
    setDialog(null);
    setReason("");
  };
  const done = (updated: Draft) => {
    close();
    onChange(updated);
  };
  const submit = useMutation({ mutationFn: () => submitDraft(draft.id), onSuccess: onChange });
  const approve = useMutation({ mutationFn: () => approveDraft(draft.id, reason.trim() || null), onSuccess: done });
  const reject = useMutation({ mutationFn: () => rejectDraft(draft.id, reason.trim()), onSuccess: done });
  const cancel = useMutation({ mutationFn: () => cancelDraft(draft.id), onSuccess: done });
  const open = !["published", "cancelled"].includes(draft.state);
  const author = draft.created_by === session.user.login || draft.submitted_by === session.user.login;
  const separation = draft.state === "pending_approval" && !draft.can_approve && author && can("policies:approve");
  const any = draft.can_submit || draft.can_approve || draft.can_publish || draft.can_cancel || draft.can_emergency_publish;
  return (
    <>
      {separation ? (
        <Notice tone="warning" title={<><UserX size={16} aria-hidden="true" /> Separation of duties</>}>
          Separation of duties: the author of a policy change cannot approve it. Another administrator must review and approve this change.
        </Notice>
      ) : null}
      {draft.state === "pending_approval" && !draft.can_approve && !separation ? <p className="muted small">Waiting for an administrator with approval permission.</p> : null}
      {draft.requires_approval && draft.state === "draft" ? <p className="muted small">This organization requires approval: submit the draft, then an administrator other than you approves it before it can be published.</p> : null}
      <ActionError error={submit.error} fallback="The draft could not be submitted." />
      {any ? (
        <div className="workflow-actions">
          {draft.can_submit ? (
            <button type="button" className="button button--primary" onClick={() => submit.mutate()} disabled={submit.isPending}>
              <Send size={14} aria-hidden="true" /> {submit.isPending ? "Submitting…" : "Submit for approval"}
            </button>
          ) : null}
          {draft.can_approve ? (
            <>
              <button type="button" className="button button--primary" onClick={() => setDialog("approve")}>
                <Check size={14} aria-hidden="true" /> Approve
              </button>
              <button type="button" className="button button--danger-outline" onClick={() => setDialog("reject")}>
                <CircleX size={14} aria-hidden="true" /> Reject
              </button>
            </>
          ) : null}
          {draft.can_publish ? (
            <button type="button" className="button button--primary" onClick={() => setDialog("publish")} disabled={draft.rebase_required}>
              <Upload size={14} aria-hidden="true" /> Publish
            </button>
          ) : null}
          {draft.can_emergency_publish ? (
            <button type="button" className="button button--danger-outline" onClick={() => setDialog("emergency")} disabled={draft.rebase_required}>
              <Siren size={14} aria-hidden="true" /> Emergency publish
            </button>
          ) : null}
          {draft.can_cancel ? (
            <button type="button" className="button button--ghost" onClick={() => setDialog("cancel")}>
              <Ban size={14} aria-hidden="true" /> Cancel draft
            </button>
          ) : null}
        </div>
      ) : (
        <p className="muted small">{open ? "Your role can review this change but not act on it." : `This draft is ${draft.state.replace("_", " ")}; it can no longer change.`}</p>
      )}

      <ConfirmDialog
        open={dialog === "approve"}
        tone="default"
        title="Approve this policy change"
        confirmLabel="Approve"
        onCancel={close}
        onConfirm={() => approve.mutate()}
        busy={approve.isPending}
      >
        <p>Your approval applies to this exact document. If the draft is edited afterwards, the approval is cancelled.</p>
        <ChangesTable changes={draft.changes} caption="Changes to approve" />
        <div className="field">
          <label htmlFor="approve-reason">Note (optional)</label>
          <textarea id="approve-reason" rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <ActionError error={approve.error} fallback="The approval could not be recorded." />
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "reject"}
        title="Reject this policy change"
        confirmLabel="Reject"
        onCancel={close}
        onConfirm={() => reject.mutate()}
        confirmDisabled={!reason.trim()}
        busy={reject.isPending}
      >
        <p>The author can edit a rejected draft and submit it again.</p>
        <div className="field">
          <label htmlFor="reject-reason">Reason (required, shown to the author and recorded)</label>
          <textarea id="reject-reason" rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <ActionError error={reject.error} fallback="The rejection could not be recorded." />
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "cancel"} title="Cancel this draft?" confirmLabel="Cancel draft" onCancel={close} onConfirm={() => cancel.mutate()} busy={cancel.isPending}>
        <p>A cancelled draft is kept for history and can no longer be edited, approved or published. Pending approval requests are cancelled.</p>
        <ActionError error={cancel.error} fallback="The draft could not be cancelled." />
      </ConfirmDialog>
      {dialog === "publish" ? <PublishDialog draft={draft} onClose={close} onDone={done} /> : null}
      {dialog === "emergency" ? <EmergencyDialog draft={draft} onClose={close} onDone={done} /> : null}
    </>
  );
}

function ProposedDocument({ draft }: { draft: Draft }) {
  const rules = [...new Set([...Object.keys(draft.floors), ...Object.keys(draft.defaults)])].sort();
  return (
    <ul className="plain-list proposed">
      {rules.map((rule) => (
        <li key={rule} className="requirement">
          <code>{rule}</code> <Requirement mandatory={draft.floors[rule] ?? null} fallback={draft.defaults[rule] ?? null} />
        </li>
      ))}
    </ul>
  );
}

function RebaseNotice({ draft, can, onChange }: { draft: Draft; can: (permission: Permission) => boolean; onChange: (draft: Draft) => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const rebase = useMutation({
    mutationFn: () => rebaseDraft(draft.id, draft.revision),
    onSuccess: (updated) => {
      setOpen(false);
      onChange(updated);
    },
  });
  return (
    <Notice tone="warning" title="The target changed since this draft was created">
      <p>
        The draft is based on v{draft.base_version}; the current version is v{draft.current_version}. Publishing it would be refused, because it would overwrite changes it has not reviewed.
        {draft.can_edit ? ` Rebase it on v${draft.current_version} to compare the proposed rules with the current version again.` : ""}
      </p>
      <div className="notice__actions">
        {draft.can_edit ? (
          <button type="button" className="button button--secondary" onClick={() => setOpen(true)}>
            <GitMerge size={14} aria-hidden="true" /> Rebase on v{draft.current_version}
          </button>
        ) : null}
        {!draft.can_edit && can("policies:write") ? (
          <button
            type="button"
            className="button button--ghost"
            onClick={() =>
              navigate(routes.newDraft({ type: draft.target.type, id: draft.target.id }), {
                state: { prefill: { floors: draft.floors, defaults: draft.defaults, title: draft.title, reason: draft.reason } },
              })
            }
          >
            <FilePlus2 size={14} aria-hidden="true" /> New draft with these rules
          </button>
        ) : null}
      </div>
      {!draft.can_edit && draft.state === "pending_approval" ? <p className="small">A draft waiting for approval cannot be rebased. Cancel it, or start a new draft with the same rules.</p> : null}
      <ConfirmDialog
        open={open}
        tone="default"
        title={`Rebase on v${draft.current_version}`}
        confirmLabel={`Rebase on v${draft.current_version}`}
        onCancel={() => {
          setOpen(false);
          rebase.reset();
        }}
        onConfirm={() => rebase.mutate()}
        busy={rebase.isPending}
      >
        <p>The proposed rules stay as they are and are compared with v{draft.current_version} again; review the new diff before publishing.</p>
        <p>Like any edit, rebasing returns the draft to DRAFT and cancels its approvals.</p>
        <ActionError
          error={rebase.error}
          fallback="The draft could not be rebased."
          onReload={() => {
            setOpen(false);
            rebase.reset();
            void queryClient.invalidateQueries({ queryKey: ["governance", "draft", draft.id] });
          }}
        />
      </ConfirmDialog>
    </Notice>
  );
}

function targetLink(draft: Draft): string {
  if (draft.target.type === "group") return routes.group(draft.target.id);
  if (draft.target.type === "repository") return routes.repository(Number(draft.target.id));
  return routes.policy(draft.organization_id);
}

function ExistingDraft({ draftId }: { draftId: string }) {
  const queryClient = useQueryClient();
  const { organizations } = useSession();
  const query = useQuery({ queryKey: ["governance", "draft", draftId], queryFn: () => getDraft(draftId) });
  useDocumentTitle(query.data?.title ?? "Policy change");
  if (query.isPending) return <SkeletonRows rows={8} label="Loading policy change…" />;
  if (query.error instanceof ApiError && query.error.status === 404) return <NotFoundContent resource="policy change" />;
  if (query.error || !query.data) return <ErrorState title="We could not load this policy change." error={query.error} onRetry={() => void query.refetch()} />;

  const draft = query.data;
  const access = organizations.find((o) => o.organization.id === draft.organization_id);
  const can = (permission: Permission) => Boolean(access?.permissions.includes(permission));
  const onChange = (updated: Draft) => {
    queryClient.setQueryData(["governance", "draft", draftId], updated);
    void queryClient.invalidateQueries({ queryKey: ["governance", draft.organization_id] });
  };
  return (
    <>
      <PageHeader
        eyebrow={<Link to={routes.policyGovernance}>Policy governance</Link>}
        title={draft.title}
        description={
          <>
            Proposed change to <Link to={targetLink(draft)}>{draft.target.label}</Link> ({TARGET_TYPE_LABEL[draft.target.type]?.toLowerCase()} policy)
          </>
        }
        actions={can("audit:read") ? <Link className="button button--secondary" to={`${routes.organizationAudit}?policy=${encodeURIComponent(draft.id)}`}>Audit trail</Link> : null}
      >
        <div className="badge-row">
          <Badge map={DRAFT_STATE} value={draft.state} />
          {draft.weakening ? <span className="tag tag--danger">Weakens enforcement</span> : null}
          {draft.emergency ? <span className="tag tag--danger">Emergency publication</span> : null}
        </div>
      </PageHeader>

      {draft.rebase_required ? <RebaseNotice draft={draft} can={can} onChange={onChange} /> : null}
      {draft.state === "published" ? (
        <Notice tone="success" title={`Published as v${draft.published_version ?? "?"}`}>
          {draft.published_by ?? "unknown"} published this change <Time value={draft.published_at} />
          {draft.emergency ? " as an emergency publication" : ""}.
          {draft.rollout_id ? (
            <>
              {" "}
              <Link to={routes.rollout(draft.rollout_id)}>Follow the staged rollout</Link>.
            </>
          ) : null}
        </Notice>
      ) : null}

      <div className="grid-2">
        <Panel title="Review" id="review">
          <KeyValueList
            items={[
              ["Target", <Link to={targetLink(draft)}>{draft.target.label}</Link>],
              ["Based on", draft.base_version ? `v${draft.base_version}` : "Nothing published (v0)"],
              ["Current version", draft.current_version ? `v${draft.current_version}` : "Nothing published"],
              ["Author", <>{draft.created_by ?? "unknown"} · <Time value={draft.created_at} /></>],
              ["Submitted", draft.submitted_at ? <>{draft.submitted_by ?? "unknown"} · <Time value={draft.submitted_at} /></> : <span className="muted">Not submitted</span>],
              ["Approval", draft.requires_approval ? "Required by the organization" : "Not required by the organization"],
              ["Reason", draft.reason ? `“${draft.reason}”` : <span className="muted">None given</span>],
              ["Revision", String(draft.revision)],
            ]}
          />
          <h3 className="subheading">Proposed policy</h3>
          <ProposedDocument draft={draft} />
        </Panel>
        <Panel title="Workflow" id="workflow">
          <Workflow draft={draft} can={can} onChange={onChange} />
          <h3 className="subheading">Approvals</h3>
          {draft.approvals.length ? (
            <ol className="plain-list approvals">
              {draft.approvals.map((a) => (
                <li key={a.id} className="approvals__item">
                  <Badge map={APPROVAL_STATUS} value={a.status} compact />
                  <span className="small">
                    Requested by {a.requested_by ?? "unknown"} <Time value={a.requested_at} />
                    {a.decided_at ? <> · {a.status} by {a.decided_by ?? "unknown"} <Time value={a.decided_at} /></> : null}
                  </span>
                  {a.reason ? <span className="muted small">“{a.reason}”</span> : null}
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted small">No approval has been requested.</p>
          )}
        </Panel>
      </div>

      <Panel title={draft.state === "published" && draft.published_version ? `Changes published in v${draft.published_version}` : "Changes from the current version"} id="changes">
        {draft.state === "published" && draft.published_version ? (
          <ChangesTable
            changes={draft.changes}
            caption={`Changes published in v${draft.published_version}`}
            fromLabel={`v${draft.published_version - 1}`}
            toLabel={`v${draft.published_version}`}
          />
        ) : (
          <>
            <ChangesTable changes={draft.changes} caption="Changes from the current version" />
            {draft.weakening ? (
              <Notice tone="warning" title="Weakening highlighted">
                Rows marked “Weakens enforcement” may allow commits that are blocked today. Publishing them needs explicit confirmation, a recent sign-in and a reason.
              </Notice>
            ) : null}
          </>
        )}
      </Panel>

      {draft.can_edit ? (
        <Panel title="Edit the draft" id="edit">
          <DraftEditor key={draft.revision} draft={draft} onSaved={onChange} />
        </Panel>
      ) : null}

      <Panel title="Simulation" id="simulation">
        <SimulationPanel draft={draft} canRun={can("policies:write")} />
      </Panel>
    </>
  );
}

export default function PolicyDraftPage() {
  const { draftId } = useParams();
  if (draftId === undefined) {
    return (
      <OrganizationGate title="New policy change" permission="policies:write">
        {(scope) => <NewDraft key={scope.id} scope={scope} />}
      </OrganizationGate>
    );
  }
  if (!HEX_ID.test(draftId)) return <NotFoundContent resource="policy change" />;
  return <ExistingDraft key={draftId} draftId={draftId} />;
}

