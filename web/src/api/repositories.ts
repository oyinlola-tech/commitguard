import { getData, getPage, send } from "./client";
import type { Page, RepositoryDetail, RepositorySummary } from "./types";

export interface RepositoryFilters {
  organization?: number | null;
  protection?: string;
  q?: string;
  sort?: string;
  cursor?: string | null;
  limit?: number;
}

export const listRepositories = (filters: RepositoryFilters): Promise<Page<RepositorySummary>> =>
  getPage<RepositorySummary>("/repositories", { ...filters });

export const getRepository = (id: number): Promise<RepositoryDetail> =>
  getData<RepositoryDetail>(`/repositories/${encodeURIComponent(String(id))}`);

export const setMonitoring = (id: number, enabled: boolean, reason: string | null, confirm: boolean) =>
  send<RepositoryDetail>("PUT", `/repositories/${encodeURIComponent(String(id))}/monitoring`, { enabled, reason, confirm });

export const refreshEnforcement = (id: number) =>
  send<RepositoryDetail>("POST", `/repositories/${encodeURIComponent(String(id))}/enforcement/refresh`);
