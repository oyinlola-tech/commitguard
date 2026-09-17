import type { ReactNode } from "react";

import { signInUrl } from "../api/auth";
import { currentPath } from "../lib/routes";
import { Notice } from "./Primitives";

export function ReauthenticateNotice({ reason }: { reason?: ReactNode }) {
  return (
    <Notice tone="warning" title="Confirm your identity">
      {reason ?? "This change weakens enforcement, so CommitGuard requires a sign-in from the last 15 minutes."}{" "}
      <a href={signInUrl(currentPath())}>Sign in again</a> and repeat the change.
    </Notice>
  );
}
