"""``commitguard ci github``: server-side enforcement inside GitHub Actions.

Reads ``GITHUB_EVENT_NAME`` / ``GITHUB_EVENT_PATH``, analyses the commits the
event introduces with the policy from a trusted commit, and exits:

* 0 - allowed (warnings are reported but pass unless ``--fail-on warn``);
* 1 - blocked by policy;
* 2 - CommitGuard could not complete the check (fails the job: fail closed).

No token, secret, network access or write permission is used.
"""

import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import ExitCode, OutputFormat, error
from commitguard.cli.render import render_ci_text, render_json
from commitguard.core.decision import Action
from commitguard.git.repository import Repository
from commitguard.github.actions import (
    annotation_commands,
    append_file,
    commands_stopped,
    render_step_summary,
    running_in_github_actions,
    step_outputs,
    workflow_command,
)
from commitguard.github.checks import build_check_output
from commitguard.github.events import load_github_event
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.validation import validate_repository_path
from commitguard.services.ci import DEFAULT_CI_MAX_COMMITS
from commitguard.services.scan import ScanRequest, ScanService
from commitguard.utils.filesystem import atomic_write_text

ci_app = typer.Typer(help="Server-side enforcement in CI systems.", no_args_is_help=True)


def _emit(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


@ci_app.command("github")
def github_command(
    event_name: Annotated[
        str | None,
        typer.Option("--event-name", help="Event name. Default: $GITHUB_EVENT_NAME."),
    ] = None,
    event_path: Annotated[
        Path | None,
        typer.Option("--event-path", help="Event payload JSON. Default: $GITHUB_EVENT_PATH."),
    ] = None,
    config: Annotated[
        str | None,
        typer.Option(
            "--config",
            help="Repository-relative policy file, read from the TRUSTED commit "
            "(never from the checked-out change). Default: .commitguard.yaml/.yml.",
        ),
    ] = None,
    fail_on: Annotated[
        Action,
        typer.Option("--fail-on", help="Fail the check at this action or above: block or warn."),
    ] = Action.BLOCK,
    max_commits: Annotated[
        int, typer.Option("--max-commits", min=1, help="Fail if more commits would be analysed.")
    ] = DEFAULT_CI_MAX_COMMITS,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", "-f", case_sensitive=False)
    ] = OutputFormat.TEXT,
    report_file: Annotated[
        Path | None, typer.Option("--report-file", help="Also write the JSON report here.")
    ] = None,
    github_output: Annotated[
        bool,
        typer.Option(
            "--github-output/--no-github-output",
            help="Write annotations, job summary and step outputs when running in GitHub Actions.",
        ),
    ] = True,
) -> None:
    """Check the commits a pull request, merge group or push introduces."""
    in_actions = github_output and running_in_github_actions()
    try:
        if fail_on is Action.ALLOW:
            raise ValueError("--fail-on must be block or warn")
        if config is not None:
            validate_repository_path(config)
        context = load_github_event(
            event_name or os.environ.get("GITHUB_EVENT_NAME"),
            event_path or _env_path("GITHUB_EVENT_PATH"),
        )
        result = ScanService().run(
            ScanRequest(
                repository=Repository.discover(),
                context=context,
                config_path=config,
                max_commits=max_commits,
                fail_on=fail_on,
            )
        )
    except Exception as exc:  # noqa: BLE001 - any failure must fail the check
        message = f"{type(exc).__name__}: {exc}" if not str(exc) else str(exc)
        if in_actions:
            _emit(
                workflow_command(
                    "error",
                    f"Security validation could not be completed: {message}",
                    title="CommitGuard could not verify repository policy",
                )
            )
        text = (
            "CommitGuard could not verify repository policy.\n"
            f"Reason: {sanitize_for_terminal(message, max_length=4000, keep_newlines=True)}\n"
            "Security validation could not be completed.\n"
            "Result: FAILED"
        )
        if in_actions:
            # The reason can quote untrusted YAML or payload text: print it to stdout
            # with workflow commands disabled (stdout/stderr ordering is not guaranteed).
            with commands_stopped(_emit):
                _emit(text)
        else:
            error(text)
        raise typer.Exit(code=int(ExitCode.ERROR)) from None

    report = result.report
    check = build_check_output(report, fail_on=fail_on)
    failed = not result.enforcement.allowed

    if output_format is OutputFormat.JSON:
        _emit(render_json(report))
    elif in_actions:
        with commands_stopped(_emit):
            _emit(render_ci_text(report, failed=failed))
    else:
        _emit(render_ci_text(report, failed=failed))

    if report_file is not None:
        atomic_write_text(report_file, render_json(report) + "\n", overwrite=True)

    if in_actions:
        if output_format is not OutputFormat.JSON:
            for line in annotation_commands(check):
                _emit(line)
        summary = _env_path("GITHUB_STEP_SUMMARY")
        if summary is not None:
            append_file(summary, render_step_summary(report, check))
        outputs = _env_path("GITHUB_OUTPUT")
        if outputs is not None:
            append_file(outputs, step_outputs(report, check))

    if failed:
        raise typer.Exit(code=int(ExitCode.BLOCKED))
