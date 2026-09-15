import { getData, getPage, send } from "./client";
import type { ExecutionHistory, Page, ScanComparison, ScanDetail, ScanSummary } from "./types";

export interface ScanFilters {
  organization?: number | null;
  repository?: number | null;
  result?: string;
  event?: string;
  rule?: string;
  severity?: string;
  from?: string;
  to?: string;
  q?: string;
  sort?: string;
  cursor?: string | null;
  limit?: number;
}

export const listScans = (filters: ScanFilters): Promise<Page<ScanSummary>> => getPage<ScanSummary>("/scans", { ...filters });
export const getScan = (id: string): Promise<ScanDetail> => getData<ScanDetail>(`/scans/${encodeURIComponent(id)}`);
export const compareScan = (id: string): Promise<ScanComparison> =>
  getData<ScanComparison>(`/scans/${encodeURIComponent(id)}/comparison`);
export const requestRescan = (id: string) => send<{ scan: string; result: "queued" }>("POST", `/scans/${encodeURIComponent(id)}/rescan`);
export const listExecutions = (id: string): Promise<ExecutionHistory> =>
  getData<ExecutionHistory>(`/scans/${encodeURIComponent(id)}/executions`);
