import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router";

import { getSession } from "../api/auth";
import { ApiError, UNAUTHENTICATED_EVENT, setCsrfToken } from "../api/client";
import type { OrganizationAccess, Permission, SessionInfo } from "../api/types";
import { LoadingState, ErrorState } from "../components/States";
import { readOrganization, writeOrganization } from "../lib/preferences";

export const SESSION_QUERY_KEY = ["session"] as const;

interface SessionContextValue {
  session: SessionInfo;
  organizations: OrganizationAccess[];
  /** The organization filter chosen in the top bar; null means all organizations. */
  organization: number | null;
  setOrganization: (id: number | null) => void;
  /** Whether the user holds `permission` in the selected organization (or in any, when all). */
  can: (permission: Permission, organizationId?: number | null) => boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <RequireSession>");
  return value;
}

/** For pages that work with or without a session (landing, login). */
export function useOptionalSession() {
  return useQuery({
    queryKey: SESSION_QUERY_KEY,
    queryFn: getSession,
    retry: false,
    staleTime: 60_000,
  });
}

/** Redirects to sign-in once when any request finds the session gone. */
export function useUnauthenticatedRedirect(): void {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useEffect(() => {
    const handler = (event: Event) => {
      const code = (event as CustomEvent<string>).detail;
      setCsrfToken(null);
      queryClient.clear();
      const returnTo = `${window.location.pathname}${window.location.search}`;
      if (window.location.pathname.startsWith("/login")) return; // never loop
      const params = new URLSearchParams({ return_to: returnTo });
      if (code === "SESSION_EXPIRED") params.set("reason", "expired");
      navigate(`/login?${params.toString()}`, { replace: true });
    };
    window.addEventListener(UNAUTHENTICATED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, handler);
  }, [navigate, queryClient]);
}

export function RequireSession({ children }: { children?: ReactNode }) {
  const location = useLocation();
  const query = useOptionalSession();
  const [organization, setOrganizationState] = useState<number | null>(readOrganization);

  useEffect(() => {
    setCsrfToken(query.data?.csrf_token ?? null);
  }, [query.data]);

  const organizations = useMemo(() => query.data?.organizations ?? [], [query.data]);
  const selected = organization !== null && organizations.some((o) => o.organization.id === organization) ? organization : null;

  const setOrganization = useCallback((id: number | null) => {
    writeOrganization(id);
    setOrganizationState(id);
  }, []);

  const can = useCallback(
    (permission: Permission, organizationId?: number | null) => {
      const scope = organizationId === undefined ? selected : organizationId;
      return organizations.some(
        (o) => (scope === null || o.organization.id === scope) && o.permissions.includes(permission),
      );
    },
    [organizations, selected],
  );

  const value = useMemo(
    () => (query.data ? { session: query.data, organizations, organization: selected, setOrganization, can } : null),
    [query.data, organizations, selected, setOrganization, can],
  );

  if (query.isPending) return <LoadingState label="Checking your session…" fullPage />;
  if (query.error) {
    if (query.error instanceof ApiError && query.error.status === 401) {
      const params = new URLSearchParams({ return_to: `${location.pathname}${location.search}` });
      if (query.error.code === "SESSION_EXPIRED") params.set("reason", "expired");
      return <Navigate to={`/login?${params.toString()}`} replace />;
    }
    return <ErrorState title="We could not load your session." error={query.error} onRetry={() => void query.refetch()} fullPage />;
  }
  return <SessionContext.Provider value={value}>{children ?? <Outlet />}</SessionContext.Provider>;
}
