# Security Policy

## Supported versions

CommitGuard is pre-alpha (`0.1.0.dev0`). Only the `main` branch receives fixes.
It does not enforce any policy yet; do not rely on it as a security control.

## Reporting a vulnerability

Please report privately using **GitHub private vulnerability reporting**
("Report a vulnerability" under the repository's *Security* tab). Do not open a
public issue, pull request or discussion.

<!-- TODO: add a security contact address once one exists. -->

Include:

- affected version or commit;
- a description and impact;
- reproduction steps or a proof of concept (a crafted commit or config is ideal);
- whether the issue is already public.

We aim to acknowledge reports within 5 working days.

## In scope

- Code or command execution through configuration, rule files, commit metadata,
  or repository contents.
- Shell / option injection into Git or other subprocesses.
- Terminal escape injection that survives CommitGuard's output sanitisation.
- **Detection bypasses**: attribution that a documented, implemented detector
  should catch but does not.
- Policy weakening: configuration that silently lowers enforcement.
- Fail-open behaviour: errors that result in ALLOW.
- Leakage of environment variables, tokens or repository content.
- CommitGuard modifying a repository or its history.

## CI and supply chain

- GitHub Actions used by CommitGuard are pinned to full commit SHAs; updates are
  made deliberately with the upstream tag noted next to the SHA.
- CI installs dependencies from `requirements/ci.txt` with `--require-hashes`.
- Reports about the GitHub layer are in scope, including ways to make the check
  pass for commits that the same policy blocks locally.

## Out of scope

- Bypassing **local** hooks (`--no-verify`, deleting hooks): documented and
  expected; see `docs/threat-model.md`.
- Merges allowed because branch protection does not require the check, or a
  pull request edited the workflow file: documented GitHub configuration issues.
- Removing attribution from a commit entirely: metadata is self-asserted, and
  CommitGuard does not claim to prove authorship.
- Features documented as not implemented.
