import { useQuery } from "@tanstack/react-query";
import { ArrowRight, LogIn, Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";

import { signInUrl } from "../../api/auth";
import { getHealth } from "../../api/client";
import { useOptionalSession } from "../../auth/session";
import { routes } from "../../lib/routes";
import { Logo, LogoMark } from "../Logo";

const LINKS = [
  ["#evidence", "What it reads"],
  ["#enforcement", "Where it enforces"],
  ["#use-cases", "Use cases"],
  ["#principles", "Security model"],
] as const;

export function PublicNavbar() {
  const session = useOptionalSession();
  const [open, setOpen] = useState(false);
  return (
    <header className="public-nav">
      <div className="public-nav__inner">
        <Link to="/" className="public-nav__brand" aria-label="CommitGuard home">
          <Logo />
        </Link>
        <nav aria-label="Site" className={open ? "public-nav__links public-nav__links--open" : "public-nav__links"} id="site-links">
          {LINKS.map(([href, label]) => (
            <a key={href} href={href} onClick={() => setOpen(false)}>
              {label}
            </a>
          ))}
        </nav>
        <div className="public-nav__cta">
          {session.data ? (
            <Link className="button button--primary" to={routes.overview}>
              Dashboard <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : (
            <a className="button button--primary" href={signInUrl(routes.overview)}>
              <LogIn size={14} aria-hidden="true" /> Sign in
            </a>
          )}
          <button type="button" className="icon-button public-nav__toggle" aria-expanded={open} aria-controls="site-links" aria-label={open ? "Close menu" : "Open menu"} onClick={() => setOpen((v) => !v)}>
            {open ? <X size={18} aria-hidden="true" /> : <Menu size={18} aria-hidden="true" />}
          </button>
        </div>
      </div>
    </header>
  );
}

export function ServiceStatus() {
  const health = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => getHealth(signal), staleTime: 30_000, refetchInterval: 60_000 });
  const state = health.isPending ? "unknown" : health.data ? "up" : "down";
  return (
    <span className="service-status">
      <span className={`status-dot status-dot--${state}`} aria-hidden="true" />
      {state === "unknown" ? "Checking service status" : state === "up" ? "Service operational" : "Service unreachable"}
    </span>
  );
}

export function PublicFooter() {
  return (
    <footer className="public-footer">
      <div className="public-footer__inner">
        <div className="public-footer__lead">
          <p className="public-footer__statement">Detection and policy decide. GitHub enforces. The dashboard explains.</p>
          <ServiceStatus />
        </div>
        <nav aria-label="Footer" className="public-footer__columns">
          <div>
            <h2>Dashboard</h2>
            <ul>
              <li><Link to={routes.overview}>Overview</Link></li>
              <li><Link to={routes.violations}>Violations</Link></li>
              <li><Link to={routes.policies}>Policies</Link></li>
              <li><Link to={routes.audit}>Audit log</Link></li>
            </ul>
          </div>
          <div>
            <h2>Enforcement</h2>
            <ul>
              <li><a href="#enforcement">Git hooks</a></li>
              <li><a href="#enforcement">GitHub Actions</a></li>
              <li><a href="#enforcement">GitHub App</a></li>
              <li><Link to={routes.rules}>Detection rules</Link></li>
            </ul>
          </div>
          <div>
            <h2>Trust</h2>
            <ul>
              <li><a href="#principles">Security model</a></li>
              <li><a href="#blocked">When a commit is blocked</a></li>
              <li><a href="#use-cases">Use cases</a></li>
            </ul>
          </div>
        </nav>
      </div>
      <p className="public-footer__wordmark" aria-hidden="true">
        <LogoMark size={64} />
        CommitGuard
      </p>
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
