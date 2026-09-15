import { API_BASE, getData, send } from "./client";
import type { SessionInfo, SessionView } from "./types";

export const getSession = (): Promise<SessionInfo> => getData<SessionInfo>("/auth/session");
export const listSessions = (): Promise<SessionView[]> => getData<SessionView[]>("/auth/sessions");
export const revokeSession = (id: string) => send<{ revoked: boolean }>("DELETE", `/auth/sessions/${encodeURIComponent(id)}`);
export const signOut = () => send<{ signed_out: boolean }>("POST", "/auth/logout");

/** Sign-in is a full-page navigation: GitHub's authorization page cannot be fetched. */
export function signInUrl(returnTo: string): string {
  const params = new URLSearchParams({ return_to: returnTo });
  return `${API_BASE}/auth/login?${params.toString()}`;
}
