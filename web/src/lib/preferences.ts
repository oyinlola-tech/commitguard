/**
 * Non-sensitive UI preferences (theme, selected organization filter).
 * Nothing security-relevant - no token, no role, no data - is stored in the
 * browser.
 */

export type ThemePreference = "system" | "light" | "dark";

const THEME_KEY = "commitguard.theme";
const ORGANIZATION_KEY = "commitguard.organization";

function storage(): Storage | null {
  try {
    // eslint-disable-next-line no-restricted-globals -- the single sanctioned use
    return window.localStorage;
  } catch {
    return null;
  }
}

export function readTheme(): ThemePreference {
  const value = storage()?.getItem(THEME_KEY);
  return value === "light" || value === "dark" ? value : "system";
}

export function writeTheme(theme: ThemePreference): void {
  const store = storage();
  if (!store) return;
  if (theme === "system") store.removeItem(THEME_KEY);
  else store.setItem(THEME_KEY, theme);
}

export function applyTheme(theme: ThemePreference): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function readOrganization(): number | null {
  const value = storage()?.getItem(ORGANIZATION_KEY);
  return value && /^\d{1,16}$/.test(value) ? Number(value) : null;
}

export function writeOrganization(id: number | null): void {
  const store = storage();
  if (!store) return;
  if (id === null) store.removeItem(ORGANIZATION_KEY);
  else store.setItem(ORGANIZATION_KEY, String(id));
}
