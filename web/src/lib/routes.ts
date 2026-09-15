/**
 * Route builders. IDs come from the API and are always percent-encoded; no
 * navigation target is ever built from repository names, commit messages or
 * other untrusted text.
 */

const segment = (value: string | number): string => encodeURIComponent(String(value));

export const routes = {
  overview: "/dashboard",
  repositories: "/repositories",
  repository: (id: number) => `/repositories/${segment(id)}`,
  scans: "/scans",
  scan: (id: string) => `/scans/${segment(id)}`,
  violations: "/violations",
  violation: (id: string) => `/violations/${segment(id)}`,
  policies: "/policies",
  policy: (organizationId: number) => `/policies/${segment(organizationId)}`,
  notifications: "/notifications",
  rules: "/rules",
  rule: (id: string) => `/rules/${segment(id)}`,
  audit: "/audit",
  installations: "/github/installations",
  installation: (id: number) => `/github/installations/${segment(id)}`,
  settings: "/settings",
  login: "/login",
};

const INTERNAL_LINK = /^\/(violations|scans|github\/installations|repositories|policies)\/[A-Za-z0-9]{1,64}$/;

/** A dashboard path from a server-built notification link, or null (never an external URL). */
export function internalLink(path: string | null | undefined): string | null {
  return path && INTERNAL_LINK.test(path) ? path : null;
}

/** Only GitHub URLs produced by the server for validated owners and names are linked. */
export function isGitHubUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname === "github.com" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

export function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}
