/**
 * The only place the dashboard talks to the network.
 *
 * - Same-origin requests with the session cookie (HttpOnly: JavaScript never
 *   sees it). No token is ever stored in localStorage or sessionStorage.
 * - Writes send the CSRF token received from `GET /api/v1/auth/session`.
 * - Errors are normalised into {@link ApiError}; 401 responses are broadcast so
 *   the session layer can redirect to sign-in exactly once.
 */

import type { Envelope, Page } from "./types";

export const API_BASE = "/api/v1";

export type ErrorCode =
  | "UNAUTHENTICATED"
  | "SESSION_EXPIRED"
  | "REAUTHENTICATION_REQUIRED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "VALIDATION_ERROR"
  | "CONFLICT"
  | "CONFIRMATION_REQUIRED"
  | "APPROVAL_REQUIRED"
  | "POLICY_VERSION_INVALID"
  | "RATE_LIMITED"
  | "CSRF_FAILED"
  | "GITHUB_UNAVAILABLE"
  | "INTERNAL_ERROR"
  | "NETWORK_ERROR"
  | string;

export class ApiError extends Error {
  readonly status: number;
  readonly code: ErrorCode;
  readonly field: string | null;
  readonly requestId: string | null;

  constructor(status: number, code: ErrorCode, message: string, field: string | null = null, requestId: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.field = field;
    this.requestId = requestId;
  }

  get unauthenticated(): boolean {
    return this.status === 401 && this.code !== "REAUTHENTICATION_REQUIRED";
  }
}

export const UNAUTHENTICATED_EVENT = "commitguard:unauthenticated";

let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

type QueryValue = string | number | boolean | null | undefined;

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  }
  const search = params.toString();
  return `${API_BASE}${path}${search ? `?${search}` : ""}`;
}

async function parseError(response: Response): Promise<ApiError> {
  let code: ErrorCode = "INTERNAL_ERROR";
  let message = "Something went wrong. Try again.";
  let field: string | null = null;
  let requestId = response.headers.get("X-Request-ID");
  try {
    const body = (await response.json()) as { error?: { code?: string; message?: string; field?: string; request_id?: string } };
    if (body.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      field = body.error.field ?? null;
      requestId = body.error.request_id ?? requestId;
    }
  } catch {
    // Not JSON (e.g. a proxy error page): keep the generic message.
  }
  return new ApiError(response.status, code, message, field, requestId);
}

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  method?: Method;
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<Envelope<T>> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }
  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      body: method === "GET" ? undefined : JSON.stringify(options.body ?? {}),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "NETWORK_ERROR", "CommitGuard could not be reached. Check your connection.");
  }
  if (!response.ok) {
    const error = await parseError(response);
    if (error.unauthenticated && path !== "/auth/session") {
      window.dispatchEvent(new CustomEvent(UNAUTHENTICATED_EVENT, { detail: error.code }));
    }
    throw error;
  }
  return (await response.json()) as Envelope<T>;
}

export async function getData<T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal): Promise<T> {
  return (await request<T>(path, { query, signal })).data;
}

export async function getPage<T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal): Promise<Page<T>> {
  const envelope = await request<T[]>(path, { query, signal });
  return {
    items: envelope.data,
    nextCursor: envelope.meta.next_cursor ?? null,
    limit: envelope.meta.limit ?? envelope.data.length,
  };
}

export async function send<T>(method: Exclude<Method, "GET">, path: string, body?: unknown): Promise<Envelope<T>> {
  return request<T>(path, { method, body });
}

/** Liveness of the CommitGuard service (`GET /health`, outside the versioned API). */
export async function getHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch("/health", { cache: "no-store", credentials: "omit", signal });
    return response.ok;
  } catch {
    return false;
  }
}
