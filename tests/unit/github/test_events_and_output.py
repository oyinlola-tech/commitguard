import pytest

from commitguard.ci.context import CIEventKind
from commitguard.core.decision import Action
from commitguard.github.actions import (
    commands_stopped,
    escape_data,
    escape_property,
    render_step_summary,
    step_outputs,
    workflow_command,
)
from commitguard.github.checks import MAX_ANNOTATIONS_PER_LEVEL, build_check_output
from commitguard.github.events import GitHubEventError, parse_github_event

A, B, Z = "a" * 40, "b" * 40, "0" * 40
REPO = {"full_name": "octo/project", "default_branch": "main"}


def pr(**head: object) -> dict[str, object]:
    return {
        "pull_request": {
            "number": 12,
            "base": {"sha": A, "ref": "main", "repo": {"full_name": "octo/project"}},
            "head": {"sha": B, "ref": "f", "repo": {"full_name": "octo/project"}, **head},
        },
        "repository": REPO,
    }


def test_pull_request() -> None:
    context = parse_github_event("pull_request", pr())
    assert context.event is CIEventKind.PULL_REQUEST
    assert (context.base_sha, context.head_sha, context.pull_request_number) == (A, B, 12)
    assert context.ref == "refs/heads/main"
    assert context.from_fork is False


def test_fork_detection() -> None:
    assert parse_github_event("pull_request", pr(repo={"full_name": "x/project"})).from_fork
    assert parse_github_event("pull_request", pr(repo=None)).from_fork  # deleted fork


def test_push_new_and_deleted_refs() -> None:
    new = parse_github_event("push", {"ref": "refs/heads/x", "before": Z, "after": A, "repository": REPO})
    assert new.before_sha is None
    assert new.after_sha == A
    gone = parse_github_event("push", {"ref": "refs/heads/x", "before": A, "after": Z, "deleted": True})
    assert gone.ref_deleted
    assert gone.after_sha is None


def test_merge_group() -> None:
    context = parse_github_event("merge_group", {"merge_group": {"base_sha": A, "head_sha": B}})
    assert context.event is CIEventKind.MERGE_GROUP


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("pull_request_target", pr()),
        ("issue_comment", {}),
        ("pull_request", []),
        ("pull_request", {"pull_request": {"number": 1, "base": {"sha": "HEAD"}, "head": {"sha": B}}}),
        ("push", {"ref": "refs/heads/x", "before": Z, "after": "-p"}),
        ("push", {"ref": "refs/heads/x" + chr(10), "before": Z, "after": A}),
        ("push", {"ref": "refs/heads/x", "before": A, "after": B, "deleted": True}),
        ("push", {"ref": "refs/heads/x", "before": A, "after": Z, "deleted": False}),
    ],
)
def test_invalid_events(name: str, payload: object) -> None:
    with pytest.raises((GitHubEventError, ValueError)):
        parse_github_event(name, payload)


def test_workflow_command_escaping() -> None:
    assert escape_data("50%\r\n") == "50%25%0D%0A"
    assert escape_property("a:b,c") == "a%3Ab%2Cc"
    line = workflow_command("error", "x\n::set-env name=A::1", title="t: 1, 2")
    assert line.count("\n") == 0
    assert line.startswith("::error title=t%3A 1%2C 2::")


def test_commands_stopped_uses_unpredictable_token() -> None:
    lines: list[str] = []
    with commands_stopped(lines.append):
        lines.append("untrusted")
    token = lines[0].removeprefix("::stop-commands::")
    assert len(token) == 32
    assert lines[-1] == f"::{token}::"


def _report(rules_by_commit: list[list[tuple[str, Action]]]):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from commitguard.core.result import Confidence, Evidence, EvidenceSource, Finding, Severity
    from commitguard.services.reports import CIReport, CommitReport, EvaluatedFinding, ScanReport

    commits = []
    for index, findings in enumerate(rules_by_commit):
        items = tuple(
            EvaluatedFinding(
                finding=Finding(
                    detector="coauthor",
                    rule_id=rule,
                    severity=Severity.HIGH,
                    confidence=Confidence.HIGH,
                    title="AI coauthor detected",
                    message="m",
                    evidence=(
                        Evidence(source=EvidenceSource.COAUTHOR_TRAILER, value="C <x> | `y` *z*"),
                    ),
                    remediation="r",
                ),
                action=action,
                policy_id=rule,
                reason="r",
            )
            for rule, action in findings
        )
        commits.append(
            CommitReport(
                commit_sha=f"{index:040x}",
                short_sha=f"{index:07x}",
                subject="s",
                action=Action.most_restrictive([a for _, a in findings]),
                findings=items,
            )
        )
    return ScanReport(
        tool_version="t",
        generated_at=datetime.now(UTC),
        repository=None,
        target="x",
        trigger="ci",
        config_sources=("builtin",),
        action=Action.most_restrictive([c.action for c in commits]),
        commits=tuple(commits),
        ci=CIReport(provider="github", event="pull_request", policy_source="base", notices=("n",)),
    )


def test_check_output_and_annotation_caps() -> None:
    report = _report([[("ai_coauthor", Action.BLOCK)] for _ in range(15)] + [[("bot_identity", Action.WARN)]])
    output = build_check_output(report)
    assert output.conclusion == "failure"
    assert output.counts["block"] == 15
    assert sum(a.level == "failure" for a in output.annotations) == MAX_ANNOTATIONS_PER_LEVEL
    assert output.omitted_annotations == 5
    assert "15 of 16 commit(s) blocked" in output.title


def test_fail_on_warn_changes_conclusion_not_findings() -> None:
    report = _report([[("bot_identity", Action.WARN)]])
    assert build_check_output(report).conclusion == "success"
    assert build_check_output(report, fail_on=Action.WARN).conclusion == "failure"


def test_summary_escapes_untrusted_values_and_outputs_are_fixed() -> None:
    report = _report([[("ai_coauthor", Action.BLOCK)]])
    output = build_check_output(report)
    summary = render_step_summary(report, output)
    assert "C &lt;x&gt; &#124; \\`y\\` \\*z\\*" in summary
    assert step_outputs(report, output) == (
        "result=block\nconclusion=failure\ncommits=1\nviolations=1\nwarnings=0\n"
    )
