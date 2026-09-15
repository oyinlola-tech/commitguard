import { useQuery } from "@tanstack/react-query";
import { ArrowRight, LogIn } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { signInUrl } from "../../api/auth";
import { getHealth } from "../../api/client";
import { useOptionalSession } from "../../auth/session";
import { routes } from "../../lib/routes";
import { Logo } from "../Logo";

export function PublicNavbar() {
  const session = useOptionalSession();
  return (
    <header className="public-nav">
      <div className="public-nav__inner">
        <Link to="/" className="public-nav__brand" aria-label="CommitGuard home">
          <Logo />
        </Link>
        <nav aria-label="Site" className="public-nav__links">
          <a href="#how-it-works">How it works</a>
          <a href="#principles">Principles</a>
          <a href="#enforcement">Enforcement</a>
        </nav>
        <div className="public-nav__cta">
          {session.data ? (
            <Link className="button button--primary" to={routes.overview}>
              Open dashboard <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : (
            <a className="button button--primary" href={signInUrl(routes.overview)}>
              <LogIn size={14} aria-hidden="true" /> Sign in with GitHub
            </a>
          )}
        </div>
      </div>
    </header>
  );
}

export function PublicFooter() {
  const health = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => getHealth(signal), staleTime: 30_000 });
  return (
    <footer className="public-footer">
      <div className="public-footer__inner">
        <div className="public-footer__brand">
          <Logo />
          <p>Commit provenance enforcement for GitHub repositories. Detection and policy run in CommitGuard core; GitHub enforces the result.</p>
          <p className="public-footer__status">
            <span className={`status-dot ${health.data === false ? "status-dot--down" : health.data ? "status-dot--up" : ""}`} aria-hidden="true" />
            {health.isPending ? "Checking service status…" : health.data ? "Service operational" : "Service unreachable"}
          </p>
        </div>
        <nav aria-label="Footer" className="public-footer__columns">
          <div>
            <h2>Dashboard</h2>
            <ul>
              <li><Link to={routes.overview}>Overview</Link></li>
              <li><Link to={routes.repositories}>Repositories</Link></li>
              <li><Link to={routes.violations}>Violations</Link></li>
              <li><Link to={routes.policies}>Policies</Link></li>
            </ul>
          </div>
          <div>
            <h2>Enforcement</h2>
            <ul>
              <li><a href="#enforcement">Git hooks</a></li>
              <li><a href="#enforcement">GitHub Actions</a></li>
              <li><a href="#enforcement">GitHub App</a></li>
            </ul>
          </div>
          <div>
            <h2>Trust</h2>
            <ul>
              <li><Link to={routes.rules}>Detection rules</Link></li>
              <li><Link to={routes.audit}>Audit log</Link></li>
              <li><a href="#principles">Security model</a></li>
            </ul>
          </div>
        </nav>
      </div>
      <div className="public-footer__legal">
        <span>CommitGuard · MIT License</span>
        <span>CommitGuard never rewrites Git history.</span>
      </div>
    </footer>
  );
}

export function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <div className="public">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <PublicNavbar />
      <main id="main" tabIndex={-1}>
        {children}
      </main>
      <PublicFooter />
    </div>
  );
}
