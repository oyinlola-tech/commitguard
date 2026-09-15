import { useQuery } from "@tanstack/react-query";
import { LogOut, Menu, Monitor, Moon, Search, Sun, X } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { signOut } from "../../api/auth";
import { getHealth } from "../../api/client";
import { getOverview } from "../../api/dashboard";
import { useSession, useUnauthenticatedRedirect } from "../../auth/session";
import { INTEGRATION } from "../../lib/labels";
import { applyTheme, readTheme, writeTheme, type ThemePreference } from "../../lib/preferences";
import { routes } from "../../lib/routes";
import { Badge } from "../Badge";
import { ErrorBoundary } from "../ErrorBoundary";
import { Logo } from "../Logo";
import { NAV_ITEMS } from "./navigation";

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  const { can, organization } = useSession();
  const overview = useQuery({
    queryKey: ["overview", "7d", organization],
    queryFn: () => getOverview("7d", organization),
    enabled: can("repositories:read"),
    staleTime: 60_000,
  });
  const openViolations = overview.data?.summary.open_violations ?? 0;
  const items = NAV_ITEMS.filter((item) => !item.permission || can(item.permission));
  return (
    <nav aria-label="Primary" className="nav">
      <ul className="nav__list">
        {items.map((item, index) => {
          const Icon = item.icon;
          const heading = item.group && item.group !== items[index - 1]?.group ? item.group : null;
          return (
            <li key={item.to}>
              {heading ? <p className="nav__group">{heading}</p> : null}
              <NavLink to={item.to} className={({ isActive }) => (isActive ? "nav__link nav__link--active" : "nav__link")} onClick={onNavigate} end={item.to === routes.overview}>
                <Icon size={16} aria-hidden="true" />
                <span>{item.label}</span>
                {item.to === routes.violations && openViolations > 0 ? (
                  <span className="nav__count" aria-label={`${openViolations} open`}>
                    {openViolations > 99 ? "99+" : openViolations}
                  </span>
                ) : null}
              </NavLink>
            </li>
          );
        })}
      </ul>
      <div className="nav__footer">
        <p className="nav__footer-label">GitHub</p>
        {overview.data ? (
          <Badge map={INTEGRATION} value={overview.data.integration.status} compact title={overview.data.integration.detail} />
        ) : (
          <span className="muted">—</span>
        )}
      </div>
    </nav>
  );
}

function OrganizationSwitcher() {
  const { organizations, organization, setOrganization } = useSession();
  const id = useId();
  if (organizations.length === 0) return null;
  return (
    <div className="org-switcher">
      <label htmlFor={id} className="visually-hidden">
        Organization
      </label>
      <select id={id} value={organization ?? ""} onChange={(e) => setOrganization(e.target.value ? Number(e.target.value) : null)}>
        {organizations.length > 1 ? <option value="">All organizations</option> : null}
        {organizations.map((o) => (
          <option key={o.organization.id} value={o.organization.id}>
            {o.organization.login}
          </option>
        ))}
      </select>
    </div>
  );
}

const RULE_IDS = new Set(["ai_coauthor", "ai_identity", "ai_trailer", "malformed_trailer", "bot_identity"]);

function GlobalSearch() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const query = value.trim().slice(0, 100);
    if (!query) return;
    const params = new URLSearchParams({ q: query });
    if (RULE_IDS.has(query)) navigate(routes.rule(query));
    else if (/^[0-9a-f]{4,64}$/i.test(query)) navigate(`${routes.scans}?${params.toString()}`);
    else navigate(`${routes.repositories}?${params.toString()}`);
    setValue("");
  };
  return (
    <form role="search" className="global-search" onSubmit={submit}>
      <label htmlFor="global-search" className="visually-hidden">
        Search repositories, commit SHAs or rule IDs
      </label>
      <Search size={14} aria-hidden="true" className="global-search__icon" />
      <input ref={inputRef} id="global-search" type="search" placeholder="Search repository, SHA or rule" value={value} maxLength={100} onChange={(e) => setValue(e.target.value)} />
      <kbd className="global-search__hint" aria-hidden="true">
        /
      </kbd>
    </form>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<ThemePreference>(readTheme);
  const next: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };
  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  return (
    <button
      type="button"
      className="icon-button"
      onClick={() => {
        const value = next[theme];
        writeTheme(value);
        applyTheme(value);
        setTheme(value);
      }}
      aria-label={`Theme: ${theme}. Switch to ${next[theme]}`}
      title={`Theme: ${theme}`}
    >
      <Icon size={16} aria-hidden="true" />
    </button>
  );
}

function UserMenu() {
  const { session } = useSession();
  const [busy, setBusy] = useState(false);
  return (
    <div className="user-menu">
      <span className="user-menu__login" title={`Signed in as ${session.user.login}`}>
        {session.user.login}
      </span>
      <button
        type="button"
        className="icon-button"
        aria-label="Sign out"
        title="Sign out"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await signOut();
          } finally {
            // A full page load drops every cached query along with the session.
            window.location.assign("/login?reason=signed_out");
          }
        }}
      >
        <LogOut size={16} aria-hidden="true" />
      </button>
    </div>
  );
}

function Drawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("button, a")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab" && panel.current) {
        const items = Array.from(panel.current.querySelectorAll<HTMLElement>("a, button, select, input"));
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer" role="dialog" aria-modal="true" aria-label="Navigation">
      <button type="button" className="drawer__scrim" aria-label="Close navigation" onClick={onClose} tabIndex={-1} />
      <div className="drawer__panel" ref={panel}>
        <div className="drawer__header">
          <Logo />
          <button type="button" className="icon-button" aria-label="Close navigation" onClick={onClose}>
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function AppFooter() {
  const health = useQuery({ queryKey: ["health"], queryFn: ({ signal }) => getHealth(signal), refetchInterval: 60_000, staleTime: 30_000 });
  return (
    <footer className="app-footer">
      <span className="app-footer__status">
        <span className={`status-dot ${health.data === false ? "status-dot--down" : health.data ? "status-dot--up" : ""}`} aria-hidden="true" />
        {health.isPending ? "Checking service status…" : health.data ? "Service operational" : "Service unreachable"}
      </span>
      <span className="app-footer__note">CommitGuard core makes the decision · GitHub enforces it · this dashboard explains it</span>
    </footer>
  );
}

export function AppShell() {
  useUnauthenticatedRedirect();
  const [drawer, setDrawer] = useState(false);
  const location = useLocation();
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="sidebar__brand">
          <Logo />
        </div>
        <Navigation />
      </aside>
      <div className="app__main">
        <header className="topbar">
          <button type="button" className="icon-button topbar__menu" aria-label="Open navigation" aria-expanded={drawer} onClick={() => setDrawer(true)}>
            <Menu size={18} aria-hidden="true" />
          </button>
          <span className="topbar__brand">
            <Logo />
          </span>
          <OrganizationSwitcher />
          <GlobalSearch />
          <div className="topbar__end">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>
        <Drawer open={drawer} onClose={() => setDrawer(false)}>
          <Navigation onNavigate={() => setDrawer(false)} />
        </Drawer>
        <main id="main" className="content" tabIndex={-1}>
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
        <AppFooter />
      </div>
    </div>
  );
}
