/**
 * Browser sign-in against the e2e harness without contacting github.com.
 *
 * Playwright cannot intercept a request that is the target of a redirect, so
 * the test intercepts CommitGuard's own `/api/v1/auth/login` response instead:
 * it lets the real server create the state cookie and GitHub authorization URL,
 * asks the harness's fake GitHub for the code the user would receive after
 * approving, and redirects the browser straight to the real callback.
 */
export async function installGitHubSignIn(context, base, userId) {
  await context.route(`${base}/api/v1/auth/login**`, async (route) => {
    const response = await route.fetch({ maxRedirects: 0 });
    const location = response.headers()["location"];
    const authorize = await fetch(`${base}/__e2e/authorize`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId(), url: location }),
    });
    const { code, state } = await authorize.json();
    const headers = { location: `${base}/api/v1/auth/callback?code=${code}&state=${encodeURIComponent(state)}` };
    const cookie = response.headersArray().find((h) => h.name.toLowerCase() === "set-cookie");
    if (cookie) headers["set-cookie"] = cookie.value;
    await route.fulfill({ status: 302, headers });
  });
  await context.route("https://github.com/**", (route) => route.abort());
}
