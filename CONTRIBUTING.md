# Contributing to CommitGuard

Thanks for your interest. CommitGuard is a security tool, so correctness and
honest documentation matter more than speed.

## Setup

```bash
./scripts/install-dev.sh
source .venv/bin/activate
```

Requires Python 3.12+ and Git 2.31+.

## Before opening a pull request

```bash
ruff check .
ruff format --check .
mypy
pytest
```

CI runs the same checks on Python 3.12 and 3.13.

## Ground rules

1. **Detectors find, policies decide.** A detector must never block, warn or
   read configuration. See `docs/detection-engine.md`.
2. **Hooks are thin.** Installed hook scripts and `commitguard hook` must not
   contain detection logic; they call `services/`.
3. **Detectors are pure.** No subprocesses, network, filesystem writes, or Git
   access. `tests/unit/test_architecture.py` enforces the import boundaries.
3. **Fail closed.** When in doubt, an error must lead to BLOCK, not ALLOW.
4. **Treat commit data as hostile.** Sanitise before display
   (`security.sanitization`), validate before use (`security.validation`).
5. **No shell.** External commands go through `utils.subprocess.run_command`
   (Git through `git.commands.run_git`) as argument lists.
6. **No new dependencies** without discussion in an issue.
7. **Do not claim unbuilt features.** Unimplemented code raises
   `NotImplementedError` with its planned phase, and docs say so.
8. **Every new rule ID** needs a default policy in `policies/defaults.py`.

## Dependencies and CI pins

- Changing runtime dependencies requires regenerating `requirements/ci.txt`
  (hashes for every published file of each pinned version; see `requirements/ci.in`).
- GitHub Actions must be pinned to full commit SHAs with the tag in a comment;
  `tests/unit/github/test_workflows_static.py` enforces this.
- Workflow `run:` scripts must receive untrusted values through `env:`, never
  `${{ }}` interpolation.

## Tests

- Unit tests in `tests/unit/` must not need a real repository.
- Tests that run Git go in `tests/integration/` (they are marked `integration`
  automatically) and use the isolated `git_repo` fixture.
- Specifications for future phases are written as tests marked
  `xfail(strict=True)` with the `phase2`/`phase3` marker. When you implement
  the feature, the test starts passing, strict xfail fails the run, and you
  remove the marker.
- Commit fixtures live in `tests/fixtures/commits/*.yaml`.
- GitHub behaviour is tested offline in `tests/integration/github/` with a bare
  "GitHub" remote and real event payloads. `test_action_scripts.py` installs
  from PyPI and runs only with `COMMITGUARD_NETWORK_TESTS=1`.

## Adding an AI identity

Open an "AI agent identity" issue with a link to a public commit showing the
attribution. Entries in `rules/ai-identities.yaml` are only `verified: true`
with such evidence.

## Security issues

Do not open public issues for vulnerabilities or detection bypasses. See
[SECURITY.md](SECURITY.md).
