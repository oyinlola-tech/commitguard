import { getData, getPage, send } from "./client";
import type { OrganizationPolicy, Page, PolicyAction, PolicyChange, PolicyDiff, PolicyPreview, PolicyVersion } from "./types";

export type Floors = Record<string, PolicyAction | null>;

export const listPolicies = (organization?: number | null): Promise<OrganizationPolicy[]> =>
  getData<OrganizationPolicy[]>("/policies", { organization });
export const getPolicy = (organization: number): Promise<OrganizationPolicy> =>
  getData<OrganizationPolicy>(`/policies/${encodeURIComponent(String(organization))}`);
export const previewPolicy = (organization: number, floors: Floors): Promise<PolicyPreview> =>
  send<PolicyPreview>("POST", `/policies/${encodeURIComponent(String(organization))}/preview`, { floors }).then((r) => r.data);
export const listPolicyVersions = (organization: number, cursor?: string | null): Promise<Page<PolicyVersion>> =>
  getPage<PolicyVersion>(`/policies/${encodeURIComponent(String(organization))}/versions`, { cursor, limit: 10 });
export const diffPolicy = (organization: number, from: number, to: number): Promise<PolicyDiff> =>
  getData<PolicyDiff>(`/policies/${encodeURIComponent(String(organization))}/diff`, { from, to });

export interface PolicyRollback {
  target_version: number;
  expected_current_version: number;
  reason: string;
  confirm: boolean;
}

export async function rollbackPolicy(
  organization: number,
  rollback: PolicyRollback,
): Promise<{ policy: OrganizationPolicy; newVersion: number; restoredVersion: number }> {
  const result = await send<OrganizationPolicy>("POST", `/policies/${encodeURIComponent(String(organization))}/rollback`, rollback);
  const meta = (result.meta.rollback ?? {}) as { new_version?: number; restored_version?: number };
  return { policy: result.data, newVersion: meta.new_version ?? result.data.version, restoredVersion: meta.restored_version ?? rollback.target_version };
}

export interface PolicyUpdate {
  expected_version: number;
  floors: Floors;
  reason: string | null;
  confirm_weakening: boolean;
}

export async function updatePolicy(organization: number, update: PolicyUpdate): Promise<{ policy: OrganizationPolicy; changes: PolicyChange[] }> {
  const result = await send<OrganizationPolicy>("PUT", `/policies/${encodeURIComponent(String(organization))}`, update);
  return { policy: result.data, changes: (result.meta.changes as PolicyChange[] | undefined) ?? [] };
}
