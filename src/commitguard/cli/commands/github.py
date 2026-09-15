"""``commitguard github setup``: local, read-only GitHub enforcement guidance.

This command does not call the GitHub API and cannot configure or verify
branch protection. It inspects workflow files and explains what must be
configured in the repository settings for the check to be authoritative.
"""

import typer

from commitguard.cli.output import ExitCode, handled_errors, info, supports_unicode
from commitguard.git.repository import Repository
from commitguard.github.workflow import (
    CHECK_NAME,
    WORKFLOW_FILE,
    WorkflowIssueLevel,
    inspect_repository_workflows,
)
from commitguard.security.sanitization import sanitize_for_terminal

github_app = typer.Typer(help="GitHub enforcement guidance (no API access).", no_args_is_help=True)

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
    symbol = {WorkflowIssueLevel.OK: "-", WorkflowIssueLevel.WARN: bang, WorkflowIssueLevel.FAIL: cross}
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
            info(f"    {symbol[issue.level]} {sanitize_for_terminal(issue.message, max_length=300)}")
    info("")
    info(GUIDANCE.format(check=CHECK_NAME))
    if failed:
        raise typer.Exit(code=int(ExitCode.ERROR))


github_app.command("setup")(setup_command)
