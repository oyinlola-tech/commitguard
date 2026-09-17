import { getData, send } from "./client";
import { org } from "./governance";
import type { BulkOperation, BulkOperationType } from "./types";

const operation = (id: string) => `/bulk-operations/${encodeURIComponent(id)}`;

export interface BulkRequest {
  type: BulkOperationType;
  repository_ids: number[];
  parameters: Record<string, unknown>;
  /** One key per submission: re-sending the same request returns the first operation. */
  idempotency_key: string;
  confirm: boolean;
}

export const listBulkOperations = (organization: number): Promise<BulkOperation[]> => getData<BulkOperation[]>(`${org(organization)}/bulk-operations`);
export const createBulkOperation = (organization: number, body: BulkRequest) =>
  send<BulkOperation>("POST", `${org(organization)}/bulk-operations`, body).then((r) => r.data);
export const getBulkOperation = (id: string): Promise<BulkOperation> => getData<BulkOperation>(operation(id));
export const cancelBulkOperation = (id: string) => send<BulkOperation>("POST", `${operation(id)}/cancel`).then((r) => r.data);
export const retryBulkOperation = (id: string) => send<BulkOperation>("POST", `${operation(id)}/retry`).then((r) => r.data);

/** A random idempotency key (32 hex characters) for one submission of a bulk dialog. */
export function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
