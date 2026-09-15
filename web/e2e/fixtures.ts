import { test as base, expect, type BrowserContext, type Page } from "@playwright/test";

export const OWNER = 501;
export const VIEWER = 502;

/**
 * Sign in through the real CommitGuard OAuth routes. github.com is never
 * contacted: the login response is intercepted and the harness's fake GitHub
 * issues the code the user would receive after approving.
 */
export async function signIn(context: BrowserContext, page: Page, userId = OWNER, returnTo = "/dashboard") {
  await context.route("**/api/v1/auth/login**", async (route) => {
    const response = await route.fetch({ maxRedirects: 0 });
    const location = response.headers()["location"] ?? "";
    const origin = new URL(route.request().url()).origin;
    const authorize = await fetch(`${origin}/__e2e/authorize`, { method: "POST", body: JSON.stringify({ user_id: userId, url: location }) });
    const { code, state } = (await authorize.json()) as { code: string; state: string };
    const cookie = response.headersArray().find((h) => h.name.toLowerCase() === "set-cookie");
    await route.fulfill({
      status: 302,
      headers: {
        location: `${origin}/api/v1/auth/callback?code=${code}&state=${encodeURIComponent(state)}`,
        ...(cookie ? { "set-cookie": cookie.value } : {}),
      },
    });
  });
  await context.route("https://github.com/**", (route) => route.abort());
  await page.goto(`/login?return_to=${encodeURIComponent(returnTo)}`);
  await page.getByRole("link", { name: /Continue with GitHub/ }).click();
  await page.waitForURL((url) => url.pathname === returnTo.split("?")[0]);
}

export async function control<T>(page: Page, path: string, body: unknown = {}): Promise<T> {
  const response = await page.request.post(`/__e2e/${path}`, { data: body });
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as T;
}

export const test = base;
export { expect };
