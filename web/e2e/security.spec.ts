import { expect, signIn, test } from "./fixtures";

test("the dashboard runs under a strict Content-Security-Policy without violations", async ({ context, page }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/.test(message.text())) violations.push(message.text());
  });
  const response = await page.goto("/");
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("script-src 'self'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  await signIn(context, page);
  for (const path of ["/dashboard", "/violations", "/policies", "/settings"]) {
    await page.goto(path);
    await page.waitForTimeout(500);
  }
  expect(violations).toEqual([]);
});

test("session credentials are not readable by page scripts or stored in the browser", async ({ context, page }) => {
  await signIn(context, page);
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "__Host-commitguard_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("commitguard_session");
  const stored = await page.evaluate(() => JSON.stringify({ ...window.localStorage, ...window.sessionStorage }));
  expect(stored).not.toMatch(/ghu_|ghs_|csrf|session|token/i);
});

test("a cross-site write without the CSRF token is refused", async ({ context, page }) => {
  await signIn(context, page);
  const status = await page.evaluate(async () => {
    const response = await fetch("/api/v1/policies/1001", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: 0, floors: {} }),
    });
    return response.status;
  });
  expect(status).toBe(403);
});
