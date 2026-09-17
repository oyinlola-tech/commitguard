<!--
Security vulnerabilities and detection bypasses: do not describe them in a
public pull request. Follow SECURITY.md first.
-->

## Summary

<!-- What does this change and why? Link the issue: "Closes #123". -->

## Type of change

- [ ] Bug fix
- [ ] New feature or behaviour change
- [ ] Detection, rules or dataset change
- [ ] Documentation only
- [ ] Tests, CI or tooling
- [ ] Dependency change (discussed in an issue first)

## Tests

<!-- What did you run? Paste the summary lines, not full logs. -->

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest` (or the relevant subset, stated below)
- [ ] `pytest -m security` (for changes to security-relevant code)
- [ ] `web/`: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build` (and `npm run e2e` where relevant); CI does not run these
- [ ] New or changed behaviour has tests; a bug fix has a test that fails without the fix

## Security impact

- [ ] No security impact, because: <!-- one sentence -->
- [ ] This touches a high-risk component listed in `.github/CODEOWNERS` (detection, provenance, rules, policy, configuration, governance, Git and hooks, GitHub integration, authentication and authorization, storage, workflows, dependencies)
- [ ] Errors still fail closed (BLOCK or exit code 2), never ALLOW
- [ ] No policy can be weakened silently (configuration, mandatory floors, trusted policy source)
- [ ] Untrusted input (commit metadata, webhook payloads, configuration, API input) is validated and sanitised
- [ ] No secrets, tokens, keys or commit messages reach logs, API responses, Check Runs or stored data
- [ ] No shell, no new network access, no new subprocess outside `utils.subprocess` / `git.commands`
- [ ] Security fixes include a regression test that carries the `security` marker (path in `SECURITY_TEST_PATHS`)
- [ ] CLI exit codes remain 0 (allowed), 1 (blocked), 2 (error)

## Documentation

- [ ] Documentation updated to match the implementation (or no user-visible change)
- [ ] Unimplemented or unverified behaviour is labelled Planned / Experimental
- [ ] `CHANGELOG.md` updated under `[Unreleased]` for user-visible changes
- [ ] Recorded benchmark results under `benchmarks/results/raw/` were not edited (new results are new files)

## Commits

This repository runs CommitGuard on itself (the `commitguard` check) with the
built-in policies: **AI co-author trailers, AI author or committer identities
and AI attribution trailers are blocked.** Remove `Co-authored-by:` lines and
"Generated with ..." footers that AI tools add before pushing.

- [ ] My commits contain no AI co-author trailers or other AI attribution, and are authored under my own identity
- [ ] I license this contribution under the MIT License (inbound = outbound, see CONTRIBUTING.md)
