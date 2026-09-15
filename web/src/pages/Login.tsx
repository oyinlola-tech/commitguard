import { LogIn } from "lucide-react";
import { Link, Navigate, useSearchParams } from "react-router";

import { signInUrl } from "../api/auth";
import { useOptionalSession } from "../auth/session";
import { Logo, LogoMark } from "../components/Logo";
import { Notice } from "../components/Primitives";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { routes } from "../lib/routes";

const ERRORS: Record<string, string> = {
  sign_in_failed: "Sign-in could not be completed. Start again from this page.",
  access_denied: "GitHub sign-in was cancelled.",
  github_unavailable: "GitHub could not be reached. Try again in a moment.",
};

/** Only same-origin application paths are accepted as return targets (the server re-checks). */
function safeReturn(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\") || value.startsWith("/api/")) {
    return routes.overview;
  }
  return value.slice(0, 512);
}

export default function Login() {
  useDocumentTitle("Sign in");
  const [params] = useSearchParams();
  const session = useOptionalSession();
  const returnTo = safeReturn(params.get("return_to"));
  if (session.data) return <Navigate to={returnTo} replace />;
  const error = params.get("error");
  const reason = params.get("reason");
  return (
    <div className="login">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="login__brand" aria-hidden="true">
        <Logo />
        <p className="login__tagline">The core makes the decision. GitHub enforces it. The dashboard explains it.</p>
        <p className="login__trace">
          {"commitguard-app  pull_request #128\n"}
          {"  scanned   3 commits  base 3a91f02..8e71c2a\n"}
          {"  policy    organization v4 + trusted .commitguard.yaml\n"}
          {"  verdict   BLOCKED  ai_coauthor  (Co-authored-by, line 9)"}
        </p>
      </aside>
      <main id="main" className="login__main" tabIndex={-1}>
        <div className="login__card">
          <LogoMark size={34} />
          <h1>Sign in to CommitGuard</h1>
          <p className="muted">Use your GitHub account. CommitGuard reads which installations and repositories you can access; it never asks for a password or a personal access token.</p>
          {reason === "expired" ? <Notice tone="warning">Your session expired. Sign in again to continue.</Notice> : null}
          {reason === "reauthenticate" ? <Notice tone="warning">Confirm your identity to finish a security-sensitive change.</Notice> : null}
          {reason === "signed_out" ? <Notice tone="success">You have signed out.</Notice> : null}
          {error ? <Notice tone="danger">{ERRORS[error] ?? ERRORS.sign_in_failed}</Notice> : null}
          <a className="button button--primary button--large button--block" href={signInUrl(returnTo)}>
            <LogIn size={16} aria-hidden="true" /> Continue with GitHub
          </a>
          <p className="login__foot">
            <Link to="/">About CommitGuard</Link>
          </p>
        </div>
      </main>
    </div>
  );
}
