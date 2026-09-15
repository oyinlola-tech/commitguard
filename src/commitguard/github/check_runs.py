"""Check Run content for the GitHub App, rendered from a :class:`ScanResult`.

The conclusion comes from :class:`~commitguard.services.enforcement.EnforcementDecision`
(BLOCK -> ``failure``; WARN -> ``success`` with warnings listed; could not
verify -> ``failure``/``timed_out``). Security violations are never reported
as successful checks. All untrusted values (identities, subjects, policy
descriptions) are Markdown-escaped and truncated; output is capped at
:data:`~commitguard.github.checks.MAX_CHECK_FINDINGS` findings with counts for
the rest, and never includes secrets or infrastructure details.
"""

from commitguard.core.decision import Action
from commitguard.github.checks import MAX_CHECK_FINDINGS, CheckRunConclusion, CheckRunOutput
from commitguard.github.markdown import escape_markdown as md
from commitguard.services.enforcement import EnforcementState, FailureKind
from commitguard.services.reports import CommitReport, EvaluatedFinding
from commitguard.services.scan import ScanResult

_REMEDIATION = (
    "Remove the prohibited attribution from the listed commits (for example by "
    "rewording them locally) and push the corrected commits. CommitGuard never "
    "rewrites history or modifies the repository."
)


def queued_output(description: str) -> CheckRunOutput:
    return CheckRunOutput(
        title="Queued",
        summary=f"CommitGuard will scan {md(description, 120)}.",
    )


def in_progress_output(commits: int, policy_source: str) -> CheckRunOutput:
    return CheckRunOutput(
        title=f"Scanning {commits} commit(s)",
        summary=(
            f"CommitGuard is scanning **{commits}** commit(s).\n\nPolicy: {md(policy_source, 200)}"
        ),
    )


def error_output(kind: FailureKind, reason: str) -> tuple[CheckRunConclusion, CheckRunOutput]:
    conclusion = (
        CheckRunConclusion.TIMED_OUT if kind is FailureKind.TIMEOUT else CheckRunConclusion.FAILURE
    )
    title = {
        FailureKind.CONFIGURATION: "Configuration error",
        FailureKind.AUTHORIZATION: "Not authorized",
        FailureKind.INFRASTRUCTURE: "Scan could not be completed",
        FailureKind.TIMEOUT: "Scan timed out",
        FailureKind.INTERNAL: "Scan could not be completed",
    }[kind]
    summary = (
        "## ❌ CommitGuard could not verify repository policy\n\n"
        f"Reason: {md(reason, 500)}\n\n"
        "Security validation could not be completed, so this check fails (fail closed). "
        "Push a new commit or reopen the pull request to scan again."
    )
    return conclusion, CheckRunOutput(title=title, summary=summary)


def _finding_lines(index: int, commit: CommitReport, item: EvaluatedFinding) -> list[str]:
    finding = item.finding
    return [
        f"#### {index}. {md(finding.title, 120)}",
        "",
        f"- **Rule:** {finding.rule_id}",
        f"- **Commit:** {md(commit.short_sha, 40)} {md(commit.subject, 120)}",
        f"- **Identity:** {md(finding.evidence[0].value, 200)}",
        f"- **Source:** {md(finding.evidence[0].source.label, 60)}",
        f"- **Severity:** {finding.severity.value}",
        f"- **Action:** {item.action.value}",
        f"- **Remediation:** {md(finding.remediation, 300)}",
        "",
    ]


def completed_output(result: ScanResult) -> tuple[CheckRunConclusion, CheckRunOutput]:
    report, stats, meta = result.report, result.statistics, result.metadata
    decision = result.enforcement
    conclusion = CheckRunConclusion(decision.check_conclusion)
    blocked = decision.state is EnforcementState.BLOCKED
    with_warnings = decision.state is EnforcementState.PASSED_WITH_WARNINGS

    if blocked:
        heading, title = "❌ CommitGuard: BLOCKED", f"Blocked: {stats.violations} violation(s)"
    elif with_warnings:
        heading, title = (
            "✅ CommitGuard: PASS (with warnings)",
            f"Passed with {stats.warnings} warning(s)",
        )
    else:
        heading, title = (
            "✅ CommitGuard: PASS",
            f"Passed: {stats.commits_scanned} commit(s) scanned",
        )

    ci = report.ci
    rows = [
        "| | |",
        "|---|---|",
        f"| Commits scanned | {stats.commits_scanned} |",
        f"| Violations | {stats.violations} |",
        f"| Warnings | {stats.warnings} |",
        f"| Detector failures | {stats.detector_failures} |",
    ]
    if ci is not None:
        rows.append(f"| Policy | {md(ci.policy_source, 200)} |")
        if ci.base_sha and ci.head_sha:
            rows.append(f"| Range | {ci.base_sha[:12]}..{ci.head_sha[:12]} |")
        elif ci.head_sha:
            rows.append(f"| Commit | {ci.head_sha[:12]} |")
    rows.append(f"| Scan ID | {meta.scan_id} |")
    summary = [f"## {heading}", "", *rows, ""]
    if not blocked and not with_warnings:
        summary += ["All configured CommitGuard policies passed.", ""]
    if ci is not None and ci.policy_weakenings:
        summary += [
            "### ⚠️ Security policy modification detected",
            "",
            "The evaluated commits attempt to weaken an existing CommitGuard policy. "
            "They were evaluated with the trusted policy; additional authorization may be "
            "required.",
            "",
            *[f"- {md(change, 200)}" for change in ci.policy_weakenings],
            "",
        ]
    if ci is not None and ci.notices:
        summary += ["### Notices", "", *[f"- {md(n, 400)}" for n in ci.notices], ""]

    ordered = sorted(report.commits, key=lambda c: -c.action.rank)
    failures = [(c, f) for c in ordered for f in c.failures]
    violations = [(c, f) for c in ordered for f in c.findings if f.action is Action.BLOCK]
    warnings = [(c, f) for c in ordered for f in c.findings if f.action is Action.WARN]

    text: list[str] = []
    if failures:
        text += ["### Detector failures", ""]
        for commit, failure in failures[:MAX_CHECK_FINDINGS]:
            text.append(
                f"- {md(commit.short_sha, 40)}: {md(failure.failure.detector, 60)} did not "
                f"complete ({md(failure.failure.message, 200)}); action {failure.action.value}"
            )
        text.append("")
    shown = 0
    for label, items in (("Violations", violations), ("Warnings", warnings)):
        if not items:
            continue
        text += [f"### {label}", ""]
        budget = max(0, MAX_CHECK_FINDINGS - shown)
        for index, (commit, item) in enumerate(items[:budget], start=1):
            text += _finding_lines(index, commit, item)
        shown += min(len(items), budget)
        if len(items) > budget:
            text += [f"{label}: {len(items)}. Showing first {budget}.", ""]
    total = len(violations) + len(warnings)
    if total > MAX_CHECK_FINDINGS:
        target = (
            f"{ci.base_sha}..{ci.head_sha}"
            if ci is not None and ci.base_sha and ci.head_sha
            else "<range>"
        )
        text += [
            f"Showing {MAX_CHECK_FINDINGS} of {total} findings. Use the CommitGuard CLI for the "
            f"complete machine-readable report: `commitguard scan {target} --format json`",
            "",
        ]
    if blocked:
        text += ["### Remediation", "", _REMEDIATION, ""]
    return conclusion, CheckRunOutput(
        title=title, summary="\n".join(summary), text="\n".join(text) or None
    )
