import { getData, getPage, send } from "./client";
import type { OrganizationPolicy, Page, PolicyAction, PolicyChange, PolicyPreview, PolicyVersion } from "./types";

export type Floors = Record<string, PolicyAction | null>;

export const listPolicies = (organization?: number | null): Promise<OrganizationPolicy[]> =>
  getData<OrganizationPolicy[]>("/policies", { organization });
export const getPolicy = (organization: number): Promise<OrganizationPolicy> =>
  getData<OrganizationPolicy>(`/policies/${encodeURIComponent(String(organization))}`);
export const previewPolicy = (organization: number, floors: Floors): Promise<PolicyPreview> =>
  send<PolicyPreview>("POST", `/policies/${encodeURIComponent(String(organization))}/preview`, { floors }).then((r) => r.data);
export const listPolicyVersions = (organization: number, cursor?: string | null): Promise<Page<PolicyVersion>> =>
  getPage<PolicyVersion>(`/policies/${encodeURIComponent(String(organization))}/versions`, { cursor, limit: 10 });

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
