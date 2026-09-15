import { CircleAlert, Inbox, Lock, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "../api/client";

export function LoadingState({ label = "Loading…", fullPage = false }: { label?: string; fullPage?: boolean }) {
  return (
    <div className={fullPage ? "state state--page" : "state"} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function SkeletonRows({ rows = 5, label = "Loading…" }: { rows?: number; label?: string }) {
  return (
    <div className="skeleton" role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton__row" aria-hidden="true" />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="state state--empty">
      <span className="state__icon" aria-hidden="true">
        {icon ?? <Inbox size={20} />}
      </span>
      <p className="state__title">{title}</p>
      {children ? <div className="state__body">{children}</div> : null}
    </div>
  );
}

export function AccessDenied({ message }: { message?: string }) {
  return (
    <div className="state state--denied" role="alert">
      <span className="state__icon" aria-hidden="true">
        <Lock size={20} />
      </span>
      <p className="state__title">Access denied</p>
      <p className="state__body">{message ?? "Your role does not include access to this section."}</p>
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong.",
  error,
  onRetry,
  fullPage = false,
}: {
  title?: string;
  error?: unknown;
  onRetry?: () => void;
  fullPage?: boolean;
}) {
  const apiError = error instanceof ApiError ? error : null;
  if (apiError?.status === 403) return <AccessDenied message={apiError.message} />;
  return (
    <div className={fullPage ? "state state--page state--error" : "state state--error"} role="alert">
      <span className="state__icon" aria-hidden="true">
        <CircleAlert size={20} />
      </span>
      <p className="state__title">{title}</p>
      {apiError ? <p className="state__body">{apiError.message}</p> : null}
      {apiError?.requestId ? <p className="state__meta">Request ID {apiError.requestId}</p> : null}
      {onRetry ? (
        <button type="button" className="button button--secondary" onClick={onRetry}>
          <RefreshCw size={14} aria-hidden="true" /> Try again
        </button>
      ) : null}
    </div>
  );
}

/** Loading / error / empty / success for one query, with the page's own wording. */
export function QueryBoundary<T>({
  query,
  loading,
  errorTitle,
  isEmpty,
  empty,
  children,
}: {
  query: { isPending: boolean; error: unknown; data: T | undefined; refetch: () => unknown };
  loading?: ReactNode;
  errorTitle: string;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <>{loading ?? <SkeletonRows />}</>;
  if (query.error || query.data === undefined) {
    return <ErrorState title={errorTitle} error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (isEmpty?.(query.data)) return <>{empty}</>;
  return <>{children(query.data)}</>;
}
