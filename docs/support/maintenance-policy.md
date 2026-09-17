# Maintenance policy

> Status: intent, not a service-level agreement. CommitGuard is pre-alpha
> (`0.1.0.dev0`), has one maintainer
> ([@oyinlola-tech](https://github.com/oyinlola-tech)), and has not published a
> release. Nothing here promises response or fix times.

Related: [compatibility matrix](compatibility.md),
[compatibility policy](compatibility-policy.md),
[SECURITY.md](../../SECURITY.md).

## Supported versions

| Line | Status |
|---|---|
| `main` | Supported. Fixes land here. |
| Commits older than `main` | Not supported. Upgrade to a newer commit; pin a full commit SHA. |
| Published releases | None exist yet. |

Once releases exist, the intent is:

- Before 1.0: only the **latest release** (and `main`) receives fixes,
  including security fixes. There are no backports.
- After 1.0: the latest minor release of the current major version receives
  fixes; security fixes may be backported to the previous minor release when
  the change is small and the maintainer has capacity. Any longer support
  window would be announced explicitly.

## Release cadence

There is no fixed schedule. The intention is to publish a release when a
coherent set of changes is complete and the release validation gate passes
([../maintainers/release-process.md](../maintainers/release-process.md)), and
to publish a patch release soon after a security fix. These are intentions,
not promises; the release process has not been exercised yet.

## Security updates

- Vulnerabilities are reported privately and handled as described in
  [SECURITY.md](../../SECURITY.md) and
  [../security/incident-response.md](../security/incident-response.md).
- A security fix lands on `main` with a regression test carrying the
  `security` marker, a `Security` entry in `CHANGELOG.md`, and, once
  releases exist, a new release. A GitHub security advisory is published
  when a fix affects users.
- Detection bypasses are security issues. The fix adds the bypass to a new
  dataset version so the benchmark records the before and after.
- Users running the GitHub App service are responsible for upgrading their
  deployment; CommitGuard has no automatic update mechanism and no telemetry,
  so the maintainer cannot know who is affected.

## Dependency updates

- Runtime dependencies are deliberately few (`typer`, `pydantic`, `PyYAML`,
  `tzdata` on Windows; `cryptography` for the App). New dependencies need an
  issue first.
- `.github/workflows/security.yml` runs `pip-audit` on every push to `main`,
  on pull requests and weekly (Monday 04:17 UTC), plus ruff security rules,
  bandit and, on pull requests, GitHub's dependency review.
- Dependabot security updates are not enabled for the repository today, and
  there is no Dependabot version-update configuration. Updates are made by
  hand.
- `requirements/ci.txt` (hash-pinned, used by the Action and this repository's
  `commitguard` workflow) is regenerated from `requirements/ci.in` whenever a
  runtime dependency changes, or when an audit finds a vulnerable pinned
  version.
- GitHub Actions are pinned to full commit SHAs with the tag in a comment.
  Updating one is a deliberate change reviewed like code.
- Dashboard dependencies (`web/package-lock.json`) are updated by hand; the
  dashboard checks are not run in CI, so updates must be verified locally with
  `npm test`, `npm run typecheck`, `npm run lint`, `npm run build` and
  `npm run e2e`.

## GitHub API changes

CommitGuard depends on GitHub's REST API (version header `2022-11-28`),
webhooks, the Checks API, GitHub App authentication and OAuth. The maintainer
watches GitHub's changelog and deprecation notices as described in
[../maintainers/repository-management.md](../maintainers/repository-management.md#github-api-change-monitoring).
When GitHub announces a breaking change or the end of an API version:

1. An issue is opened describing the impact on the Action, the App service and
   the dashboard.
2. The offline GitHub model used by the tests is updated to the new behaviour
   first, so the tests fail where CommitGuard is affected.
3. The fix is released before GitHub's announced date where possible.

Because the App service is tested against an offline model and not against
github.com, changes on GitHub's side can break it without the tests noticing.
Reports from people running it are especially valuable.

## Git compatibility

- The minimum Git versions are enforced in code: 2.31 for the CLI and hooks,
  2.45 for the App service.
- Raising a minimum is an incompatible change; it is announced in
  `CHANGELOG.md` with the reason (usually a Git feature needed for safe
  parsing or offline analysis) and reported by `commitguard doctor` /
  `commitguard github validate`.
- CI uses the Git version of the GitHub-hosted runner images, which is not
  pinned. There is no CI job that tests the minimum Git versions today.
- Git behaviour CommitGuard relies on for security (`--end-of-options`,
  `GIT_NO_REPLACE_OBJECTS`, `--no-use-mailmap`, `GIT_NO_LAZY_FETCH`, hook
  execution through `sh`) is covered by integration tests, so a Git change
  that breaks it should fail CI on the next runner image update.

## Python compatibility

- Supported Python versions are those tested in CI (3.12 and 3.13 today).
- A new Python version is added to the CI matrix and `pyproject.toml`
  classifiers once its tests pass.
- Dropping a Python version is an incompatible change, announced in
  `CHANGELOG.md`, and not done while that version is still maintained upstream
  unless a dependency requires it.

## Deprecation

Deprecations follow the process in
[compatibility-policy.md](compatibility-policy.md#deprecation-process):
announce in `CHANGELOG.md`, warn where possible, keep working for at least one
minor release (before 1.0), then remove with migration steps.

## End of maintenance

If the project stops being maintained, the intent is to say so at the top of
`README.md`, archive the repository on GitHub, and leave the code and
documentation available under the MIT License.
