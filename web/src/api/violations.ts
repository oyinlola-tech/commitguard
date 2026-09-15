import { getData, getPage, send } from "./client";
import type { Page, ViolationDetail, ViolationSummary } from "./types";

export interface ViolationFilters {
  organization?: number | null;
  repository?: number | null;
  status?: string;
  severity?: string;
  rule?: string;
  action?: string;
  from?: string;
  to?: string;
  q?: string;
  sort?: string;
  cursor?: string | null;
  limit?: number;
}

export const listViolations = (filters: ViolationFilters): Promise<Page<ViolationSummary>> =>
  getPage<ViolationSummary>("/violations", { ...filters });
export const getViolation = (id: string): Promise<ViolationDetail> =>
  getData<ViolationDetail>(`/violations/${encodeURIComponent(id)}`);
export const acknowledgeViolation = (id: string, note: string | null) =>
  send<ViolationDetail>("PUT", `/violations/${encodeURIComponent(id)}/acknowledgement`, { note });
export const removeAcknowledgement = (id: string) =>
  send<ViolationDetail>("DELETE", `/violations/${encodeURIComponent(id)}/acknowledgement`);
