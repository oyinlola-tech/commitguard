# Maintainer security duties

## Protected components

Changes to these need the most careful review, because a mistake in them is a
security failure rather than a bug. They are also the paths listed in
[.github/CODEOWNERS](../../.github/CODEOWNERS).

| Component | Path | Why |
|---|---|---|
| Trailer and identity parsing | `provenance/` | Three of the four known detection bypasses were here |
| Normalisation | `provenance/normalization.py` | Decides what "the same name" means; invisible-character bypasses live here |
| Detectors and rules | `detectors/`, `rules/` | False negatives are silent |
| Policy engine | `policies/` | Precedence and the mandatory floor; a weakening bug is invisible |
| Webhook verification | `github/webhooks.py` | Signature verification and delivery de-duplication |
| GitHub authentication | `github/auth.py`, `github/settings.py` | App JWT, installation tokens, key handling |
| Authorization | `controlplane/` | Roles, tenant isolation, sessions, CSRF |
| Storage | `github/storage.py`, `governance/` | SQL construction, retention, audit integrity |
| Subprocess and validation | `utils/subprocess.py`, `security/validation.py` | Argument vectors, injection defences |
| Secrets and redaction | `security/secrets.py` | Leaks and the ReDoS surface on log text |
| Workflows and the Action | `.github/workflows/`, `action.yml` | Supply chain and fork pull request safety |

### Review checklist for those paths

1. Does anything now return "allow" on a path that previously raised? Fail-closed
   behaviour is asserted in `tests/security/test_properties.py`.
2. Can untrusted input reach a new regular expression? If so, add it to the ReDoS
   inventory (it is found automatically, but the budget must still hold).
3. Does a new parser raise something other than its documented error type? The
   fuzzers assert this.
4. Is a new identity, trailer form or normalisation step covered by a **new**
   dataset version rather than an edit to an existing one?
5. Does any new output path print untrusted text without
   `sanitize_for_terminal` / `escape_markdown`?
6. Does any new log field carry a token, a key or a full commit message?

## Keys and secrets

The maintainer holds no production secrets for anyone else's deployment. For the
project itself:

- CI uses **no secrets**; if that ever changes, the workflow that uses them must
  not run fork pull request code.
- The demo stack uses generated, throw-away values only.
- If a key for a hosted demo ever exists, it is rotated on any suspicion, and the
  procedure is in [../security/incident-response.md](../security/incident-response.md).

## Before every merge to `main`

`ruff check`, `ruff format --check`, `mypy`, the full `pytest` run, and
`pytest -m security`. CI runs all of them; running them locally first is faster
than a red build.

## Security reports

Triage per [../security/vulnerability-response.md](../security/vulnerability-response.md).
The rule that matters: **a confirmed vulnerability is not fixed until a test
fails without the fix.**
