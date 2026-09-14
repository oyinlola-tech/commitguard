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
