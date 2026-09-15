"""Rendering of :class:`ScanReport` objects as text or JSON.

* ``scan`` text: detailed, human-oriented explanation of every finding.
* ``check`` text: one tab-separated line per finding plus a ``result=`` line.
* JSON: the full report model, ASCII-only (all non-ASCII and control
  characters escaped, so it is terminal-safe), schema_version 1.
"""

import json

from commitguard.cli.output import supports_unicode
from commitguard.core.decision import Action
from commitguard.core.result import Evidence, MatchReason
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.services.reports import CommitReport, EvaluatedFinding, ScanReport


def _s(text: str, limit: int = 300) -> str:
    return sanitize_for_terminal(text, max_length=limit)


def render_json(report: ScanReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=True, sort_keys=False)


def _symbols() -> tuple[str, str, str]:
    return ("✓", "✗", "!") if supports_unicode() else ("OK", "X", "!")


def _reason(reason: MatchReason) -> str:
    return f'{reason.kind.value} "{_s(reason.value, 120)}" ({_s(reason.rule, 120)})'


def _evidence_lines(evidence: Evidence) -> list[str]:
    source = evidence.source.label
    if evidence.line_number is not None:
        source += f", line {evidence.line_number}"
    lines = [f"  Evidence:    {_s(evidence.value)}", f"  Source:      {source}"]
    for index, reason in enumerate(evidence.matched):
        lines.append(f"  {'Matched:' if index == 0 else '':<12} {_reason(reason)}")
    if evidence.notes:
        lines.append(f"  Notes:       {_s('; '.join(evidence.notes))}")
    return lines


def _finding_block(item: EvaluatedFinding, commit: CommitReport) -> list[str]:
    finding = item.finding
    policy = f" (policy {item.policy_id})" if item.policy_id else ""
    lines = [
        _s(finding.title, 120),
        f"  Commit:      {commit.short_sha}",
        f"  Detector:    {finding.detector}",
        f"  Rule:        {finding.rule_id}",
        f"  Severity:    {finding.severity.value}",
        f"  Confidence:  {finding.confidence.value}",
        f"  Action:      {item.action.value}{policy}",
        f"  Message:     {_s(finding.message)}",
    ]
    for evidence in finding.evidence:
        lines.extend(_evidence_lines(evidence))
    lines.append(f"  Remediation: {_s(finding.remediation)}")
    return lines


def render_scan_text(report: ScanReport) -> str:
    ok, cross, bang = _symbols()
    lines = ["CommitGuard"]
    if report.repository:
        lines.append(f"Repository: {_s(report.repository)}")
    count = len(report.commits)
    lines.append(
        f"Target:     {_s(report.target, 120)} ({count} commit{'s' if count != 1 else ''})"
    )
    lines.append("Config:     " + " < ".join(_s(source, 200) for source in report.config_sources))
    lines.append("")

    ordered = [(item, commit) for commit in report.commits for item in commit.findings]
    ordered.sort(key=lambda pair: -pair[0].action.rank)  # stable: commit order kept per action
    failures = [(failure, commit) for commit in report.commits for failure in commit.failures]

    if not ordered and not failures:
        lines.append(f"{ok} Repository scanned")
        lines.append(f"{ok} No policy violations detected")
    else:
        headline = {
            Action.BLOCK: f"{cross} BLOCKED: policy violation detected",
            Action.WARN: f"{bang} WARNING: policy warnings detected",
            Action.ALLOW: f"{ok} Findings present, all allowed by policy",
        }[report.action]
        lines.append(headline)
        for failure, commit in failures:
            lines.append("")
            lines.append(f"{cross} Detector failure (scan incomplete)")
            lines.append(f"  Commit:      {commit.short_sha}")
            lines.append(f"  Detector:    {failure.failure.detector}")
            lines.append(
                f"  Error:       {_s(failure.failure.error_type)}: {_s(failure.failure.message)}"
            )
            lines.append(f"  Action:      {failure.action.value} ({_s(failure.reason)})")
        for item, commit in ordered:
            lines.append("")
            lines.extend(_finding_block(item, commit))

    summary = report.summary
    lines.append("")
    lines.append(
        f"Summary: {summary['commits']} commit(s), {summary['block']} blocking, "
        f"{summary['warn']} warning(s), {summary['allow']} allowed finding(s)"
    )
    lines.append(f"Result: {report.action.value.upper()}")
    return "\n".join(lines)


def render_check_text(report: ScanReport) -> str:
    """Stable, grep/cut-friendly output: ``ACTION<TAB>sha<TAB>detector<TAB>rule<TAB>evidence``."""
    lines = []
    for commit in report.commits:
        for failure in commit.failures:
            lines.append(
                "\t".join(
                    [
                        failure.action.value.upper(),
                        commit.short_sha,
                        failure.failure.detector,
                        "detector_failure",
                        _s(failure.failure.message, 200),
                    ]
                )
            )
        for item in commit.findings:
            evidence = item.finding.evidence[0].value
            lines.append(
                "\t".join(
                    [
                        item.action.value.upper(),
                        commit.short_sha,
                        item.finding.detector,
                        item.finding.rule_id,
                        _s(evidence, 200),
                    ]
                )
            )
    summary = report.summary
    lines.append(
        f"result={report.action.value.upper()} commits={summary['commits']} "
        f"block={summary['block']} warn={summary['warn']} allow={summary['allow']}"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Hook output (written to stderr by the hook commands)
# --------------------------------------------------------------------------- #
_PUSH_REMEDIATION = [
    "How to fix:",
    "  CommitGuard never modifies commits. Rewrite the blocked commits so they no",
    "  longer carry the attribution, then push again:",
    "  - latest commit only:  git commit --amend",
    "      (replaces that one local commit with an edited copy)",
    "  - older commits:       git rebase -i <commit>^   and mark them 'reword'",
    "      (recreates the selected commits and every commit after them)",
    "  Only rewrite commits that have not already been shared with others.",
]


def _short_finding_lines(item: EvaluatedFinding) -> list[str]:
    finding = item.finding
    evidence = finding.evidence[0]
    where = evidence.source.label + (
        f", line {evidence.line_number}" if evidence.line_number is not None else ""
    )
    return [
        f"    {_s(finding.title, 120)} [{finding.rule_id}, {finding.severity.value}]",
        f"    Evidence: {_s(evidence.value, 160)} ({where})",
    ]


def _commit_marker(action: Action) -> str:
    ok, cross, bang = _symbols()
    return {Action.BLOCK: cross, Action.WARN: bang, Action.ALLOW: ok}[action]


def render_push_text(report: ScanReport, *, remote: str | None, verbose: bool) -> str:
    ok, cross, bang = _symbols()
    commits = report.commits
    counts = {action: sum(1 for c in commits if c.action is action) for action in Action}
    lines = ["CommitGuard"]
    if not commits:
        lines.append(f"{ok} No new commits to check for this push.")
        return "\n".join(lines)

    if report.action is Action.ALLOW and not any(c.findings or c.failures for c in commits):
        count = len(commits)
        return (
            f"CommitGuard: {ok} {count} outgoing commit{'s' if count != 1 else ''} checked, "
            "no policy violations"
        )
    if report.action is Action.BLOCK:
        lines.append(f"{cross} PUSH BLOCKED")
    elif report.action is Action.WARN:
        lines.append(f"{bang} PUSH ALLOWED WITH WARNINGS")
    else:
        lines.append(f"{ok} Push allowed: no policy violations")
    lines += [
        f"Remote: {_s(remote or '?', 200)}",
        f"Commits checked: {len(commits)}",
        f"Violations: {counts[Action.BLOCK]}",
        f"Warnings: {counts[Action.WARN]}",
        f"Allowed: {counts[Action.ALLOW]}",
    ]

    flagged = [c for c in commits if c.findings or c.failures]
    for commit in sorted(flagged, key=lambda c: -c.action.rank):
        lines.append("")
        lines.append(
            f"{_commit_marker(commit.action)} {commit.short_sha}  {_s(commit.subject, 72)}"
        )
        for failure in commit.failures:
            lines.append(
                f"    Detector failure: {failure.failure.detector} "
                f"({_s(failure.failure.message, 160)}); analysis incomplete"
            )
        for item in commit.findings:
            if verbose:
                lines.extend("  " + line for line in _finding_block(item, commit))
            else:
                lines.extend(_short_finding_lines(item))
                if item.action is not Action.BLOCK:
                    lines.append(f"    Action: {item.action.value}")

    if report.action is Action.BLOCK:
        lines += ["", *_PUSH_REMEDIATION, "", "No changes were pushed to the remote repository."]
        if not verbose:
            lines.append("Full evidence: commitguard scan <sha>   (for each blocked commit)")
    return "\n".join(lines)


def render_commit_hook_text(report: ScanReport, *, stage: str, verbose: bool) -> str:
    ok, cross, bang = _symbols()
    (commit,) = report.commits
    if not commit.findings and not commit.failures:
        return ""
    lines = ["CommitGuard"]
    if report.action is Action.BLOCK:
        lines.append(f"{cross} COMMIT BLOCKED ({stage})")
    elif report.action is Action.WARN:
        lines.append(f"{bang} Commit allowed with warnings ({stage})")
    else:
        lines.append(f"{ok} Findings allowed by policy ({stage})")
    for failure in commit.failures:
        lines.append(
            f"  Detector failure: {failure.failure.detector} ({_s(failure.failure.message, 160)})"
        )
    for item in commit.findings:
        lines.append("")
        if verbose:
            lines.extend(_finding_block(item, commit))
        else:
            lines.extend(line[2:] for line in _short_finding_lines(item))
            lines.append(f"  Action: {item.action.value}")
    if report.action is Action.BLOCK:
        lines.append("")
        lines.append("Nothing was committed. Your staged changes are unchanged.")
        if stage == "commit-msg":
            lines.append("Remove the attribution from the message and commit again.")
            lines.append(
                "Your message is still in .git/COMMIT_EDITMSG; to reuse it: "
                "git commit -e -F .git/COMMIT_EDITMSG"
            )
        else:
            lines.append(
                "Commit under the responsible human contributor's identity "
                "(git config user.name / user.email, or --author)."
            )
    return "\n".join(lines)
