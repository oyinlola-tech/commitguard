import { getData, getPage } from "./client";
import type { AuditEvent, Page } from "./types";

export interface AuditFilters {
  organization?: number | null;
  repository?: number | null;
  type?: string;
  actor?: string;
  from?: string;
  to?: string;
  sort?: string;
  cursor?: string | null;
  limit?: number;
}

export const listAudit = (filters: AuditFilters): Promise<Page<AuditEvent>> => getPage<AuditEvent>("/audit", { ...filters });
export const getAuditEvent = (id: string): Promise<AuditEvent> => getData<AuditEvent>(`/audit/${encodeURIComponent(id)}`);
