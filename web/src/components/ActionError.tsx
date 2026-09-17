import type { ReactNode } from "react";

import { ApiError } from "../api/client";
import { Notice } from "./Primitives";
import { ReauthenticateNotice } from "./Reauthenticate";

/**
 * The server's answer to a refused change, in words: a stale sign-in, someone
 * else's newer version, a required approval or confirmation, a rate limit.
 * Messages come from the API and are rendered as text.
 */
export function ActionError({
  error,
  fallback = "The change could not be saved.",
  onReload,
  reauthenticateReason,
}: {
  error: unknown;
  fallback?: string;
  onReload?: () => void;
  reauthenticateReason?: ReactNode;
}) {
  if (!error) return null;
  if (!(error instanceof ApiError)) return <Notice tone="danger">{fallback}</Notice>;
  switch (error.code) {
    case "REAUTHENTICATION_REQUIRED":
      return <ReauthenticateNotice reason={reauthenticateReason} />;
    case "CONFLICT":
      return (
        <Notice tone="warning" title="This was changed by someone else">
          {error.message}
          {onReload ? (
            <>
              {" "}
              <button type="button" className="link-button" onClick={onReload}>
                Load the latest version
              </button>
            </>
          ) : null}
        </Notice>
      );
    case "APPROVAL_REQUIRED":
      return (
        <Notice tone="warning" title="Approval required">
          {error.message}
        </Notice>
      );
    case "CONFIRMATION_REQUIRED":
      return (
        <Notice tone="warning" title="Confirmation required">
          {error.message}
        </Notice>
      );
    case "RATE_LIMITED":
      return (
        <Notice tone="warning" title="Too many requests">
          {error.message} Wait a minute before trying again.
        </Notice>
      );
    case "FORBIDDEN":
      return (
        <Notice tone="danger" title="Not allowed">
          {error.message}
        </Notice>
      );
    default:
      return <Notice tone="danger">{error.message}</Notice>;
  }
}

export const isApiError = (error: unknown, code: string): error is ApiError => error instanceof ApiError && error.code === code;
