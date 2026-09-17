/**
 * Scoped policies and their workflow: draft -> simulate -> review -> approve ->
 * publish (optionally as a staged rollout). What the caller may do is decided
 * by the server and returned as `can_*` flags on each draft.
 */

import { getData, request, send } from "./client";
import { org } from "./governance";
import type {
  PolicyAction,
  PolicyDiff,
  PolicyDraft,
  PolicyTargetType,
  PolicyTargets,
  PropagationStatus,
  Rollout,
  RolloutRequest,
  Simulation,
  TargetVersions,
  DraftState,
} from "./types";

const draft = (id: string) => `/policy-drafts/${encodeURIComponent(id)}`;
const rollout = (id: string) => `/rollouts/${encodeURIComponent(id)}`;

export const listPolicyTargets = (organization: number): Promise<PolicyTargets> => getData<PolicyTargets>(`${org(organization)}/policies`);

const targetPath = (organization: number, type: PolicyTargetType) => `${org(organization)}/policy-targets/${encodeURIComponent(type)}`;

export async function listTargetVersions(
  organization: number,
  type: PolicyTargetType,
  targetId: string | null,
  cursor?: string | null,
): Promise<TargetVersions & { nextCursor: string | null }> {
  const envelope = await request<TargetVersions>(`${targetPath(organization, type)}/versions`, {
    query: { target_id: type === "organization" ? undefined : targetId, cursor, limit: 10 },
  });
  return { ...envelope.data, nextCursor: envelope.meta.next_cursor ?? null };
}

export interface TargetRollback {
  target_version: number;
  expected_current_version: number;
  reason: string;
  confirm: boolean;
}

export const rollbackTarget = (organization: number, type: PolicyTargetType, targetId: string | null, body: TargetRollback) =>
  request<{ version: number; restored_version: number; diff: PolicyDiff }>(`${targetPath(organization, type)}/rollback`, {
    method: "POST",
    query: { target_id: type === "organization" ? undefined : targetId },
    body,
  }).then((r) => r.data);

export const listDrafts = (organization: number, state?: DraftState): Promise<PolicyDraft[]> =>
  getData<PolicyDraft[]>(`${org(organization)}/policy-drafts`, { state });
export const getDraft = (id: string): Promise<PolicyDraft> => getData<PolicyDraft>(draft(id));

export interface DraftDocument {
  floors: Record<string, PolicyAction>;
  defaults: Record<string, PolicyAction>;
  title: string;
  reason: string | null;
}

export const createDraft = (organization: number, type: PolicyTargetType, targetId: string | null, document: DraftDocument) =>
  send<PolicyDraft>("POST", `${org(organization)}/policy-drafts`, {
    target_type: type,
    target_id: type === "organization" ? null : targetId,
    ...document,
  }).then((r) => r.data);
export const updateDraft = (id: string, expectedRevision: number, document: DraftDocument) =>
  send<PolicyDraft>("PATCH", draft(id), { expected_revision: expectedRevision, ...document }).then((r) => r.data);
/** Base the draft on the target's current version; like any edit, it returns to DRAFT and loses its approval. */
export const rebaseDraft = (id: string, expectedRevision: number) =>
  send<PolicyDraft>("PATCH", draft(id), { expected_revision: expectedRevision, rebase: true }).then((r) => r.data);
export const submitDraft = (id: string) => send<PolicyDraft>("POST", `${draft(id)}/submit`).then((r) => r.data);
export const approveDraft = (id: string, reason: string | null) => send<PolicyDraft>("POST", `${draft(id)}/approve`, { reason }).then((r) => r.data);
export const rejectDraft = (id: string, reason: string) => send<PolicyDraft>("POST", `${draft(id)}/reject`, { reason }).then((r) => r.data);
export const cancelDraft = (id: string) => send<PolicyDraft>("POST", `${draft(id)}/cancel`).then((r) => r.data);
/** `reason` (optional) overrides the draft's reason on the published version. */
export const publishDraft = (id: string, confirmWeakening: boolean, rolloutRequest: RolloutRequest | null, reason: string | null = null) =>
  send<PolicyDraft>("POST", `${draft(id)}/publish`, {
    confirm_weakening: confirmWeakening,
    ...(reason ? { reason } : {}),
    ...(rolloutRequest ? { rollout: rolloutRequest } : {}),
  }).then((r) => r.data);
export const emergencyPublishDraft = (id: string, reason: string, confirmWeakening: boolean) =>
  send<PolicyDraft>("POST", `${draft(id)}/emergency-publish`, { reason, confirm_weakening: confirmWeakening }).then((r) => r.data);

export const simulateDraft = (id: string, periodDays: number) =>
  send<Simulation>("POST", `${draft(id)}/simulations`, { period_days: periodDays }).then((r) => r.data);
export const listSimulations = (organization: number, draftId?: string): Promise<Simulation[]> =>
  getData<Simulation[]>(`${org(organization)}/simulations`, { draft: draftId });
export const getSimulation = (id: string): Promise<Simulation> => getData<Simulation>(`/simulations/${encodeURIComponent(id)}`);

export const listRollouts = (organization: number, active = false): Promise<Rollout[]> =>
  getData<Rollout[]>(`${org(organization)}/rollouts`, { active: active ? "true" : undefined });
export const getRollout = (id: string): Promise<Rollout> => getData<Rollout>(rollout(id));
export const advanceRollout = (id: string) => send<Rollout>("POST", `${rollout(id)}/advance`).then((r) => r.data);
export const pauseRollout = (id: string, reason: string) => send<Rollout>("POST", `${rollout(id)}/pause`, { reason }).then((r) => r.data);
export const resumeRollout = (id: string) => send<Rollout>("POST", `${rollout(id)}/resume`).then((r) => r.data);
export const rollbackRollout = (id: string, reason: string) =>
  send<Rollout>("POST", `${rollout(id)}/rollback`, { reason, confirm: true }).then((r) => r.data);

export const getPropagation = (organization: number): Promise<PropagationStatus> =>
  getData<PropagationStatus>(`${org(organization)}/policy-propagation`);
