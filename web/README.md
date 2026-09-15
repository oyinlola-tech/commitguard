# CommitGuard dashboard

The web dashboard for the CommitGuard control plane: React 19, TypeScript and
Vite. It renders results the CommitGuard core produced and the `/api/v1` API
exposes; it never detects attribution or decides PASS or BLOCK itself.

User and operator documentation: [docs/dashboard.md](../docs/dashboard.md).

## Development

```bash
npm ci
npm run dev          # http://localhost:5173, proxies /api and /health to 127.0.0.1:8080
```

Run the CommitGuard service on port 8080 with `COMMITGUARD_ENV=development` and
`COMMITGUARD_DASHBOARD_URL=http://localhost:5173` (see the docs), or point the
proxy elsewhere with `COMMITGUARD_API_URL`.

## Checks

```bash
npm test             # Vitest + Testing Library: components and pages
npm run typecheck    # strict TypeScript
npm run lint         # ESLint with react-hooks and jsx-a11y (strict)
npm run build        # production bundle in dist/
npm run e2e          # Playwright against tests/e2e/dashboard_harness.py (build first)
```

`npm run e2e` starts the real service (GitHub App, workers, real Git, SQLite,
API) with an offline model of GitHub. Set `CHROMIUM_PATH=/usr/bin/chromium` to
use an installed Chromium instead of `npx playwright install chromium`.

## Structure

```text
src/
├── api/          typed client (the only fetch) and one module per resource
├── auth/         session context, permissions, sign-in redirects
├── components/   design system: badges, tables, dialog, filters, states, layout
├── hooks/        URL state, backoff polling, document title
├── lib/          status vocabulary, formatting, route builders, preferences
├── pages/        one file per route
├── styles/       tokens (light and dark), base, application, public site
└── test/         fixtures, fetch mock, page tests
e2e/              Playwright: primary scenario, responsive, accessibility, security
```

Rules the code follows:

- Server data lives in TanStack Query; filters and cursors live in the URL.
- Every status comes from the API and is shown with the same word, icon and
  tone everywhere (`src/lib/labels.ts`), never as colour alone.
- Untrusted text (commit metadata, repository names) is rendered as text only;
  `dangerouslySetInnerHTML` is forbidden by lint and by a test.
- No credentials in browser storage; only the theme and organization filter
  are stored locally.
- Builds contain no inline scripts, so the dashboard runs under a strict
  Content-Security-Policy.
