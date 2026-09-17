import { getData, request, send } from "./client";
import { org } from "./governance";
import type { ExceptionStatus, Page, PolicyException, PolicyTargetType } from "./types";

const exception = (id: string) => `/exceptions/${encodeURIComponent(id)}`;

export interface ExceptionFilters {
  status?: ExceptionStatus | "";
  rule?: string;
  repository?: number | null;
  cursor?: string | null;
  limit?: number;
}

export async function listExceptions(organization: number, filters: ExceptionFilters): Promise<Page<PolicyException>> {
  const envelope = await request<PolicyException[]>(`${org(organization)}/exceptions`, { query: { ...filters } });
  return { items: envelope.data, nextCursor: envelope.meta.next_cursor ?? null, limit: envelope.meta.limit ?? envelope.data.length };
}

export interface ExceptionRequest {
  rule_id: string;
  scope_type: PolicyTargetType;
  scope_id: string | number | null;
  action: "warn" | "allow";
  reason: string;
  /** ISO 8601; omitted for a permanent exception. */
  expires_at?: string;
  permanent: boolean;
}

export const requestException = (organization: number, body: ExceptionRequest) =>
  send<PolicyException>("POST", `${org(organization)}/exceptions`, body).then((r) => r.data);
export const getException = (id: string): Promise<PolicyException> => getData<PolicyException>(exception(id));
export const approveException = (id: string, note: string | null) => send<PolicyException>("POST", `${exception(id)}/approve`, { note }).then((r) => r.data);
export const rejectException = (id: string, note: string) => send<PolicyException>("POST", `${exception(id)}/reject`, { note }).then((r) => r.data);
export const revokeException = (id: string, reason: string) => send<PolicyException>("POST", `${exception(id)}/revoke`, { reason }).then((r) => r.data);
export const cancelException = (id: string) => send<PolicyException>("POST", `${exception(id)}/cancel`).then((r) => r.data);
