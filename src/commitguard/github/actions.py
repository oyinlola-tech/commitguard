"""GitHub Actions runtime integration: workflow commands, job summary, outputs.

Everything printed in a GitHub Actions log can be interpreted as a workflow
command (``::set-env``, ``::add-mask::`` ...). Commit messages, author names and
branch names are attacker-controlled, so:

* human-readable output containing untrusted text is wrapped in
  ``::stop-commands::<random token>`` ... ``::<token>::``;
* annotation messages and properties are escaped exactly as ``@actions/core``
  does, after control characters are made visible;
* the job summary is Markdown with every untrusted value escaped;
* ``$GITHUB_OUTPUT`` only ever receives enum values and integers.

No GitHub API calls and no token are needed.
"""

import os
import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from commitguard.core.decision import Action
from commitguard.github.checks import AnnotationLevel, CheckOutput
from commitguard.github.markdown import escape_markdown
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.services.reports import ScanReport

MAX_SUMMARY_FINDINGS = 50
_COMMAND = {
    AnnotationLevel.FAILURE: "error",
    AnnotationLevel.WARNING: "warning",
    AnnotationLevel.NOTICE: "notice",
}


def running_in_github_actions(environ: Mapping[str, str] | None = None) -> bool:
    return (environ if environ is not None else os.environ).get("GITHUB_ACTIONS") == "true"


def escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(value: str) -> str:
    return escape_data(value).replace(":", "%3A").replace(",", "%2C")


def workflow_command(command: str, message: str, **properties: str) -> str:
    safe_message = escape_data(sanitize_for_terminal(message, max_length=1000))
    props = ",".join(
        f"{key}={escape_property(sanitize_for_terminal(value, max_length=200))}"
        for key, value in properties.items()
    )
    return f"::{command}{' ' + props if props else ''}::{safe_message}"


def annotation_commands(output: CheckOutput) -> list[str]:
    lines = [
        workflow_command(_COMMAND[a.level], a.message, title=a.title) for a in output.annotations
    ]
    if output.omitted_annotations:
        lines.append(
            workflow_command(
                "notice",
                f"{output.omitted_annotations} more finding(s) not annotated; see the job summary",
                title="CommitGuard",
            )
        )
    return lines


@contextmanager
def commands_stopped(emit: Callable[[str], None]) -> Iterator[None]:
    """Disable workflow command processing while untrusted text is printed."""
    token = secrets.token_hex(16)
    emit(f"::stop-commands::{token}")
    try:
        yield
    finally:
        emit(f"::{token}::")


_md = escape_markdown


def render_step_summary(report: ScanReport, output: CheckOutput) -> str:
    ci = report.ci
    icon = {"failure": "❌", "success": "✅"}[output.conclusion]
    lines = [f"## {icon} {_md(output.title)}", ""]
    if ci is not None:
        lines += [
            "| | |",
            "|---|---|",
            f"| Event | {_md(ci.event)}"
            + (f" (PR \\#{ci.pull_request_number})" if ci.pull_request_number else "")
            + (" from a fork" if ci.from_fork else "")
            + " |",
            f"| Range | {_md((ci.base_sha or '')[:12])}..{_md((ci.head_sha or '')[:12])} |",
            f"| Policy source | {_md(ci.policy_source)} |",
        ]
    counts = output.counts
    lines += [
        f"| Commits scanned | {counts['commits']} |",
        f"| Violations | {counts['block']} |",
        f"| Warnings | {counts['warn']} |",
        f"| Allowed | {counts['allow']} |",
        f"| Result | **{report.action.value.upper()}** |",
        "",
    ]
    if ci is not None and ci.notices:
        lines += ["### Notices", ""] + [f"- {_md(n, 400)}" for n in ci.notices] + [""]

    rows = []
    for commit in sorted(report.commits, key=lambda c: -c.action.rank):
        for failure in commit.failures:
            rows.append(
                f"| {_md(commit.short_sha)} | detector failure | {_md(failure.failure.detector)} "
                f"| - | {_md(failure.failure.message)} | {failure.action.value} |"
            )
        for item in commit.findings:
            rows.append(
                f"| {_md(commit.short_sha)} | {_md(item.finding.title)} | {item.finding.rule_id} "
                f"| {item.finding.severity.value} | {_md(item.finding.evidence[0].value)} "
                f"| {item.action.value} |"
            )
    if rows:
        lines += [
            "### Findings",
            "",
            "| Commit | Finding | Rule | Severity | Evidence | Action |",
            "|---|---|---|---|---|---|",
            *rows[:MAX_SUMMARY_FINDINGS],
        ]
        if len(rows) > MAX_SUMMARY_FINDINGS:
            lines.append(f"\n{len(rows) - MAX_SUMMARY_FINDINGS} more finding(s) omitted.")
        lines.append("")
    if report.action is Action.BLOCK:
        lines += [
            "### Remediation",
            "",
            "Update the listed commits so that they comply with the repository's contribution "
            "policy, then push the updated branch. CommitGuard does not rewrite Git history.",
            "",
        ]
    return "\n".join(lines) + "\n"


def append_file(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def step_outputs(report: ScanReport, output: CheckOutput) -> str:
    counts = output.counts
    values = {
        "result": report.action.value,
        "conclusion": output.conclusion,
        "commits": counts["commits"],
        "violations": counts["block"],
        "warnings": counts["warn"],
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())
