"""``commitguard github``: GitHub Actions guidance and GitHub App operations.

* ``setup``        - local, read-only Actions workflow guidance (no API access);
* ``validate``     - check GitHub App configuration and, unless ``--offline``,
                     authenticate to GitHub and verify permissions and installations;
* ``webhook-test`` - verify and normalise a webhook payload locally (no network,
                     no scan, no repository code);
* ``serve``        - run the GitHub App webhook service (development server).

No command configures or verifies branch protection, and no command prints the
private key, the webhook secret, JWTs or installation tokens.
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from commitguard.cli.output import ExitCode, handled_errors, info, supports_unicode
from commitguard.config.sources import load_mandatory_policy
from commitguard.exceptions.base import CommitGuardError
from commitguard.git.repository import Repository
from commitguard.github.errors import WebhookValidationError, safe_text
from commitguard.github.events import (
    IgnoredEvent,
    InstallationEvent,
    InstallationRepositoriesEvent,
    PullRequestEvent,
    PushEvent,
    normalize_webhook,
)
from commitguard.github.permissions import (
    REQUIRED_PERMISSIONS,
    WEBHOOK_EVENTS,
    excessive_permissions,
    missing_permissions,
)
from commitguard.github.pull_requests import disposition
from commitguard.github.settings import (
    ENV_DATA_DIR,
    ENV_MANDATORY_POLICY_FILE,
    load_settings,
    private_key_file_too_open,
    read_app_id,
    read_private_key,
    read_webhook_secret,
)
from commitguard.github.webhooks import MAX_WEBHOOK_BYTES, parse_json_object, verify_signature
from commitguard.github.workflow import (
    CHECK_NAME,
    WORKFLOW_FILE,
    WorkflowIssueLevel,
    inspect_repository_workflows,
)
from commitguard.observability.logging import configure_json_logging
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.utils.filesystem import read_bytes_limited

if TYPE_CHECKING:  # the App needs the optional 'app' extra (cryptography) and HTTP modules
    from commitguard.github.auth import AppCredentials
    from commitguard.github.client import Transport

github_app = typer.Typer(
    help="GitHub enforcement: Actions setup guidance and the CommitGuard GitHub App.",
    no_args_is_help=True,
)

# Replaced in tests; None means HTTPS to api.github.com via the standard library.
transport_factory: "Callable[[], Transport] | None" = None


def _app_modules_available() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        raise CommitGuardError(
            "the GitHub App needs the optional dependencies: pip install 'commitguard[app]'"
        ) from None


GUIDANCE = """\
To make the check authoritative (repository Settings -> Rules / Branches):

  1. Protect the target branch (e.g. main) with a ruleset or branch protection rule.
  2. Require a pull request before merging; do not allow bypassing, and
     block direct pushes (restrict who can push / "Restrict updates").
  3. Require status checks to pass, and add the check "{check}".
     GitHub lists it under that job name after the workflow has run once.
  4. If you use a merge queue, keep the merge_group trigger in the workflow.
  5. Protect the enforcement files: require code owner review for
     .github/workflows/ and .commitguard.yaml (CODEOWNERS), because a pull
     request can edit the workflow that checks it. Organisations can instead
     require the workflow from a separate repository with a ruleset.

Without these settings the workflow only reports; it does not prevent merges.
A workflow triggered by push runs after the commits are already on GitHub.
CommitGuard cannot verify these settings locally."""


def setup_command() -> None:
    """Show GitHub workflow status, the required check name and setup steps."""
    ok, cross, bang = ("✓", "✗", "⚠") if supports_unicode() else ("OK", "X", "!")
    symbol = {
        WorkflowIssueLevel.OK: "-",
        WorkflowIssueLevel.WARN: bang,
        WorkflowIssueLevel.FAIL: cross,
    }
    with handled_errors():
        repository = Repository.discover()
        inspections = inspect_repository_workflows(repository.root)

    info("CommitGuard GitHub setup")
    info("")
    if not inspections:
        info(f"{cross} No workflow runs CommitGuard.")
        info(
            f"    Create {WORKFLOW_FILE.as_posix()} with: commitguard init --github "
            "--action-repository OWNER/REPO --action-ref <commit sha>"
        )
        raise typer.Exit(code=int(ExitCode.ERROR))
    failed = False
    for inspection in inspections:
        path = inspection.path.relative_to(repository.root).as_posix()
        info(f"{ok} {sanitize_for_terminal(path)}")
        for name in inspection.check_names:
            info(f"    Required check name: {sanitize_for_terminal(name)}")
        for issue in inspection.issues:
            failed = failed or issue.level is WorkflowIssueLevel.FAIL
            info(
                f"    {symbol[issue.level]} {sanitize_for_terminal(issue.message, max_length=300)}"
            )
    info("")
    info(GUIDANCE.format(check=CHECK_NAME))
    if failed:
        raise typer.Exit(code=int(ExitCode.ERROR))


github_app.command("setup")(setup_command)


class _Checklist:
    def __init__(self) -> None:
        self.ok, self.cross, self.bang = ("✓", "✗", "⚠") if supports_unicode() else ("OK", "X", "!")
        self.failed = False

    def passed(self, label: str, detail: str = "") -> None:
        info(f"{self.ok} {label}" + (f": {detail}" if detail else ""))

    def fail(self, label: str, detail: str) -> None:
        self.failed = True
        info(f"{self.cross} {label}: {safe_text(detail, 400)}")

    def warn(self, label: str, detail: str) -> None:
        info(f"{self.bang} {label}: {safe_text(detail, 400)}")

    def skip(self, label: str, detail: str) -> None:
        info(f"- {label}: {detail}")


@github_app.command("validate")
def validate_command(
    installation_id: Annotated[
        int | None,
        typer.Option("--installation-id", min=1, help="Also mint a token for this installation."),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Check local configuration only; do not contact GitHub."),
    ] = False,
) -> None:
    """Validate GitHub App configuration, authentication, installations and permissions."""
    env = os.environ
    check = _Checklist()
    info("CommitGuard GitHub Configuration")
    info("")

    app_id = None
    try:
        app_id = read_app_id(env)
        check.passed("App ID")
    except CommitGuardError as exc:
        check.fail("App ID", str(exc))

    credentials = None
    try:
        _app_modules_available()
        from commitguard.github.auth import AppCredentials

        credentials = AppCredentials(app_id or 1, read_private_key(env))
        check.passed("Private key")
        if private_key_file_too_open(env):
            check.warn("Private key", "the key file is readable by other users (chmod 600)")
    except CommitGuardError as exc:
        check.fail("Private key", str(exc))

    try:
        read_webhook_secret(env)
        check.passed("Webhook secret")
    except CommitGuardError as exc:
        check.fail("Webhook secret", str(exc))

    try:
        from commitguard.github.repositories import require_mirror_git

        require_mirror_git()
        check.passed("Git version")
    except CommitGuardError as exc:
        check.fail("Git version", str(exc))

    data_dir = env.get(ENV_DATA_DIR, "")
    if data_dir and Path(data_dir).is_absolute():
        check.passed("Data directory")
    else:
        check.fail("Data directory", f"{ENV_DATA_DIR} must be set to an absolute path")

    policy_file = env.get(ENV_MANDATORY_POLICY_FILE)
    if policy_file:
        try:
            load_mandatory_policy(Path(policy_file))
            check.passed("Mandatory policy")
        except CommitGuardError as exc:
            check.fail("Mandatory policy", str(exc))

    if offline:
        check.skip("GitHub authentication", "skipped (--offline)")
        info("")
        info("Status:")
        info("NOT READY" if check.failed else "CONFIGURATION VALID (GitHub not contacted)")
        raise typer.Exit(code=int(ExitCode.ERROR if check.failed else ExitCode.OK))

    if credentials is None or app_id is None:
        check.skip("GitHub authentication", "not attempted (fix the configuration above)")
    else:
        _validate_online(check, credentials, installation_id)

    info("")
    info("Status:")
    info("NOT READY" if check.failed else "READY")
    raise typer.Exit(code=int(ExitCode.ERROR if check.failed else ExitCode.OK))


def _validate_online(
    check: _Checklist, credentials: "AppCredentials", installation_id: int | None
) -> None:
    from commitguard.github.auth import InstallationTokenProvider
    from commitguard.github.client import GitHubClient

    client = GitHubClient(transport_factory() if transport_factory else None)
    try:
        app = client.get_app(credentials.create_jwt())
        check.passed("GitHub authentication", f"App {safe_text(app.slug, 80)}")
    except CommitGuardError as exc:
        check.fail("GitHub authentication", str(exc))
        return

    missing = missing_permissions(app.permissions)
    if missing:
        check.fail(
            "Required permissions",
            "missing " + ", ".join(f"{k}: {v}" for k, v in missing.items()),
        )
    else:
        check.passed(
            "Required permissions", ", ".join(f"{k}: {v}" for k, v in REQUIRED_PERMISSIONS.items())
        )
    extra = excessive_permissions(app.permissions)
    if extra:
        check.warn(
            "Least privilege",
            "not needed by CommitGuard: " + ", ".join(f"{k}: {v}" for k, v in extra.items()),
        )
    always_sent = {"installation", "installation_repositories"}
    missing_events = sorted(set(WEBHOOK_EVENTS) - set(app.events) - always_sent)
    if missing_events:
        # GitHub always delivers installation events to Apps; they are not listed.
        check.fail("Webhook events", "not subscribed: " + ", ".join(missing_events))
    else:
        check.passed("Webhook events")

    try:
        installations = client.list_app_installations(credentials.create_jwt())
    except CommitGuardError as exc:
        check.fail("Installation access", str(exc))
        return
    if not installations:
        check.fail("Installation access", "the App is not installed on any account")
        return
    selected = [i for i in installations if installation_id in (None, i.id)]
    if installation_id is not None and not selected:
        check.fail("Installation access", f"installation {installation_id} not found")
        return
    for item in selected:
        suspended = " (suspended)" if item.suspended_at else ""
        label = (
            f"installation {item.id} ({item.account.type.value} {item.account.login}){suspended}"
        )
        installation_missing = missing_permissions(item.permissions)
        if installation_missing:
            check.fail(
                "Installation permissions",
                f"{label} has not granted " + ", ".join(installation_missing),
            )
    if installation_id is None:
        check.passed("Installation access", f"{len(installations)} installation(s)")
        return
    try:
        provider = InstallationTokenProvider(credentials, client)
        token = provider.token(installation_id, None)
        repositories = client.list_installation_repositories(token.token)
        check.passed(
            "Installation access",
            f"installation {installation_id}: {len(repositories)} repository(ies) accessible",
        )
        provider.invalidate(installation_id)
    except CommitGuardError as exc:
        check.fail("Installation access", str(exc))


@github_app.command("webhook-test")
def webhook_test_command(
    payload: Annotated[Path, typer.Argument(help="Webhook payload JSON file.", dir_okay=False)],
    event: Annotated[str, typer.Option("--event", help="X-GitHub-Event value, e.g. push.")],
    signature: Annotated[
        str | None,
        typer.Option(
            "--signature",
            help="X-Hub-Signature-256 value to verify with COMMITGUARD_GITHUB_WEBHOOK_SECRET.",
        ),
    ] = None,
) -> None:
    """Verify and normalise a webhook payload locally (no network, no scan)."""
    check = _Checklist()
    info("CommitGuard webhook test")
    info("")
    try:
        body = read_bytes_limited(payload, max_bytes=MAX_WEBHOOK_BYTES)
    except (OSError, CommitGuardError) as exc:
        check.fail("Payload", str(exc) if isinstance(exc, CommitGuardError) else "cannot be read")
        raise typer.Exit(code=int(ExitCode.ERROR)) from None
    if signature is None:
        check.skip("Signature", "not checked (pass --signature to verify)")
    else:
        try:
            verify_signature(read_webhook_secret(os.environ), body, signature)
            check.passed("Signature")
        except CommitGuardError as exc:
            check.fail("Signature", str(exc))
    try:
        normalized = normalize_webhook(event, parse_json_object(body))
        check.passed("Payload", f"{event} event is well-formed")
    except WebhookValidationError as exc:
        check.fail("Payload", str(exc))
        raise typer.Exit(code=int(ExitCode.ERROR)) from None

    if isinstance(normalized, IgnoredEvent):
        info(f"  Result: ignored ({safe_text(normalized.reason, 100)})")
    elif isinstance(normalized, InstallationEvent):
        info(f"  Installation: {normalized.installation_id} ({normalized.account.login})")
        info(f"  Action: {normalized.action.value}")
    elif isinstance(normalized, InstallationRepositoriesEvent):
        info(f"  Installation: {normalized.installation_id} ({normalized.account.login})")
        info(f"  Repositories added: {len(normalized.added)}, removed: {len(normalized.removed)}")
    else:
        info(f"  Installation: {normalized.installation_id}")
        info(f"  Repository: {normalized.repository.full_name} (id {normalized.repository.id})")
        ctx = normalized.context
        if isinstance(normalized, PullRequestEvent):
            info(f"  Pull request: #{normalized.number} ({normalized.action})")
            info(f"  Range: {(ctx.base_sha or '')[:12]}..{(ctx.head_sha or '')[:12]}")
            info(f"  Result: {disposition(normalized).value}")
        elif isinstance(normalized, PushEvent):
            info(f"  Ref: {safe_text(ctx.ref or '', 200)}")
            if ctx.ref_deleted:
                info("  Result: ignored (branch deleted)")
            else:
                info(
                    f"  Commits: {(ctx.before_sha or 'new ref')[:12]}..{(ctx.after_sha or '')[:12]}"
                )
                info("  Result: scan")
    raise typer.Exit(code=int(ExitCode.ERROR if check.failed else ExitCode.OK))


@github_app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8080,
) -> None:
    """Run the GitHub App service: webhooks, and the dashboard when configured.

    Put a TLS-terminating reverse proxy in front of it.
    """
    with handled_errors():
        _app_modules_available()
        from commitguard.api.hosting import build_dashboard, create_server_app
        from commitguard.api.settings import dashboard_enabled, load_dashboard_settings
        from commitguard.github.app import GitHubAppService
        from commitguard.github.server import serve

        configure_json_logging()
        service = GitHubAppService.from_settings(load_settings())
        dashboard = (
            build_dashboard(service, load_dashboard_settings()) if dashboard_enabled() else None
        )
    service.start()
    try:
        serve(create_server_app(service, dashboard), host=host, port=port)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
