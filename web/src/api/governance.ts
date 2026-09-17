/**
 * Organization governance: posture, the repository matrix, settings, events,
 * reports, onboarding and a repository's effective policy. Every value here -
 * posture, compliance, drift, propagation - is computed by the server.
 */

import { API_BASE, getData, request, send } from "./client";
import type {
  Acknowledgement,
  EffectivePolicyView,
  MatrixPage,
  OrganizationDetail,
  OrganizationPosture,
  OrganizationSettings,
  ReportKind,
  RepositoryMode,
  RepositoryPosture,
  SearchResult,
  SecurityEvent,
  SecurityExceptions,
  SecurityPolicies,
  SettingsView,
  TrendsView,
} from "./types";

export const org = (id: number) => `/organizations/${encodeURIComponent(String(id))}`;

export const getOrganization = (organization: number): Promise<OrganizationDetail> => getData<OrganizationDetail>(org(organization));
export const getSecurityOverview = (organization: number): Promise<OrganizationPosture> =>
  getData<OrganizationPosture>(`${org(organization)}/security/overview`);

export const MATRIX_FILTERS = ["q", "group", "posture", "protection", "mode", "onboarding", "policy_state", "drift", "exceptions", "severity", "last_scan", "sort"] as const;
export type MatrixFilters = Partial<Record<(typeof MATRIX_FILTERS)[number], string>> & { cursor?: string | null; limit?: number };

export async function listRepositoryMatrix(organization: number, filters: MatrixFilters): Promise<MatrixPage> {
  const envelope = await request<RepositoryPosture[]>(`${org(organization)}/security/repositories`, { query: { ...filters } });
  return {
    items: envelope.data,
    nextCursor: envelope.meta.next_cursor ?? null,
    limit: envelope.meta.limit ?? envelope.data.length,
    total: typeof envelope.meta.total === "number" ? envelope.meta.total : envelope.data.length,
    computedAt: typeof envelope.meta.computed_at === "string" ? envelope.meta.computed_at : null,
  };
}

export const getSecurityPolicies = (organization: number): Promise<SecurityPolicies> =>
  getData<SecurityPolicies>(`${org(organization)}/security/policies`);
export const getSecurityExceptions = (organization: number): Promise<SecurityExceptions> =>
  getData<SecurityExceptions>(`${org(organization)}/security/exceptions`);
export const getTrends = (organization: number, days: number): Promise<TrendsView> =>
  getData<TrendsView>(`${org(organization)}/security/trends`, { days });
export const listSecurityEvents = (organization: number): Promise<SecurityEvent[]> =>
  getData<SecurityEvent[]>(`${org(organization)}/security/events`);
export const acknowledgeEvent = (organization: number, eventId: string, note: string | null) =>
  send<Acknowledgement>("POST", `${org(organization)}/security/events/${encodeURIComponent(eventId)}/acknowledge`, { note });
export const searchOrganization = (organization: number, q: string): Promise<SearchResult[]> =>
  getData<SearchResult[]>(`${org(organization)}/search`, { q });

/** A same-origin download link; the session cookie authorizes it and the server audits the export. */
export function reportUrl(organization: number, kind: ReportKind, format: "json" | "csv"): string {
  return `${API_BASE}${org(organization)}/reports/${encodeURIComponent(kind)}?format=${format}`;
}

export const getSettings = (organization: number): Promise<SettingsView> => getData<SettingsView>(`${org(organization)}/settings`);

export interface SettingsUpdate {
  expected_version: number;
  settings: Partial<OrganizationSettings>;
  reason: string | null;
  confirm: boolean;
}

export const updateSettings = (organization: number, update: SettingsUpdate) =>
  send<SettingsView>("PUT", `${org(organization)}/settings`, update).then((r) => r.data);

export interface ModeChange {
  repository_ids: number[];
  mode: RepositoryMode;
  confirm: boolean;
  reason: string | null;
}

export const onboardRepositories = (organization: number, change: ModeChange) =>
  send<{ changed: number[] }>("POST", `${org(organization)}/repositories/onboard`, change).then((r) => r.data);
export const setRepositoryMode = (organization: number, change: ModeChange) =>
  send<{ changed: number[] }>("POST", `${org(organization)}/repositories/mode`, change).then((r) => r.data);

export async function getEffectivePolicy(repository: number): Promise<{ view: EffectivePolicyView; exceptions: { active: number; expiring_soon: number } | null }> {
  const envelope = await request<EffectivePolicyView>(`/repositories/${encodeURIComponent(String(repository))}/effective-policy`);
  const exceptions = envelope.meta.exceptions as { active: number; expiring_soon: number } | undefined;
  return { view: envelope.data, exceptions: exceptions ?? null };
}
