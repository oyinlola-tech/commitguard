"""Mandatory policies, enforcement mapping, Check Run output and permissions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from commitguard.ci.context import CIContext, CIEventKind, CIProvider
from commitguard.config.loader import parse_config
from commitguard.config.sources import PolicySource, PolicySourceKind, load_mandatory_policy
from commitguard.core.context import ScanTrigger
from commitguard.core.decision import Action
from commitguard.exceptions.configuration import ConfigurationError
from commitguard.git.commit import Commit
from commitguard.git.ranges import CommitRange
from commitguard.github.check_runs import completed_output, error_output
from commitguard.github.checks import (
    MAX_CHECK_FINDINGS,
    MAX_CHECK_TEXT_CHARS,
    CheckRunConclusion,
    CheckRunOutput,
    CheckRunStatus,
    check_run_update_payload,
)
from commitguard.github.permissions import (
    REQUIRED_PERMISSIONS,
    excessive_permissions,
    missing_permissions,
)
from commitguard.policies.defaults import default_policy_set
from commitguard.policies.loader import build_policy_set
from commitguard.policies.mandatory import apply_mandatory_policies
from commitguard.provenance.author import Identity
from commitguard.services.analysis import Analyzer
from commitguard.services.ci import CIPlan
from commitguard.services.enforcement import (
    EnforcementService,
    EnforcementState,
    FailureKind,
)
from commitguard.services.reports import CIReport, ScanReport
from commitguard.services.scan import ScanMetadata, ScanResult, statistics_for

HUMAN = Identity(name="Jane Dev", email="jane@example.com")


# --------------------------------------------------------------------------- #
# Mandatory policy floor
# --------------------------------------------------------------------------- #
def test_repository_cannot_weaken_mandatory_policy() -> None:
    repo = parse_config(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"
        "  ai_identity:\n    enabled: false\n  bot_identity:\n    action: block\n"
    )
    mandatory = parse_config(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: block\n  ai_identity: {}\n"
        "  bot_identity:\n    action: warn\n"
    )
    effective = apply_mandatory_policies(build_policy_set(repo), mandatory)
    assert effective["ai_coauthor"].action is Action.BLOCK
    assert effective["ai_identity"].enabled
    assert effective["ai_identity"].action is Action.BLOCK
    assert effective["bot_identity"].action is Action.BLOCK  # the floor never relaxes
    assert effective["malformed_trailer"] == default_policy_set()["malformed_trailer"]


def test_mandatory_policy_cannot_disable(tmp_path: Path) -> None:
    path = tmp_path / "org.yaml"
    path.write_text("version: 1\npolicies:\n  ai_coauthor:\n    enabled: false\n")
    with pytest.raises(ConfigurationError, match="enabled: false is not allowed"):
        load_mandatory_policy(path)
    path.write_text("version: 1\nenforcement:\n  pre_push: false\n")
    with pytest.raises(ConfigurationError, match="hook enforcement"):
        load_mandatory_policy(path)
    path.write_text("version: 1\npolicies:\n  ai_coauthor:\n    action: block\n")
    policy = load_mandatory_policy(path, description="organisation policy")
    assert policy.description == "organisation policy"
    assert len(policy.fingerprint) == 64


@pytest.mark.parametrize(
    "text",
    [
        "version: 1\npolicies: !!python/object/apply:os.system ['touch /tmp/pwned']\n",
        "version: 1\npolicies:\n  ai_coauthor: &a {action: block}\n  ai_identity: *a\n",
        "version: 1\npolicies:\n  ai_coauthor:\n    action: block\n"
        "  ai_coauthor:\n    action: allow\n",
        "version: '1'\n",
        "version: 1\npolicies:\n  ai_coauthor:\n    action: [block]\n",
        "version: 1\npolicies:\n  unknown_rule:\n    action: allow\n",
        "version: 1\n" + "x:\n " * 2000 + "y",
        "version: 1\npolicies:\n  ai_coauthor:\n    action: " + "b" * 100_000 + "\n",
        "- just\n- a list\n",
    ],
    ids=[
        "object-injection",
        "aliases",
        "duplicate-keys",
        "string-version",
        "wrong-type",
        "unknown-rule",
        "deep-nesting",
        "huge-value",
        "not-a-mapping",
    ],
)
def test_malicious_policy_yaml_is_rejected(text: str, tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    with pytest.raises(ConfigurationError):
        load_mandatory_policy(path)
    assert not Path("/tmp/pwned").exists()  # noqa: S108 - canary path, never created


# --------------------------------------------------------------------------- #
# Enforcement mapping and Check Run output
# --------------------------------------------------------------------------- #
def _result(commits: list[Commit], *, weakenings: tuple[str, ...] = ()) -> ScanResult:
    analyzer = Analyzer.create(default_policy_set())
    reports = tuple(analyzer.analyze(c, ScanTrigger.CI) for c in commits)
    report = ScanReport(
        tool_version="test",
        generated_at=datetime.now(UTC),
        repository=None,
        target="a..b",
        trigger="ci",
        config_sources=("builtin",),
        action=Action.most_restrictive([r.action for r in reports]),
        commits=reports,
        ci=CIReport(
            provider="github",
            event="pull_request",
            base_sha="a" * 40,
            head_sha="b" * 40,
            policy_source="pull request base (aaaaaaaaaaaa)",
            policy_weakenings=weakenings,
            notices=("note <b>bold</b>",),
        ),
    )
    plan = CIPlan(
        range=CommitRange(base="a" * 40, head="b" * 40),
        policy_source=PolicySource(kind=PolicySourceKind.BUILTIN, description="builtin"),
        policy_weakenings=weakenings,
    )
    return ScanResult(
        report=report,
        plan=plan,
        statistics=statistics_for(report),
        metadata=ScanMetadata(
            scan_id="s" * 32,
            tool_version="test",
            rules_version="r",
            policy_version="p",
            policy_source="builtin",
            provider="github",
            event="pull_request",
            base_sha="a" * 40,
            head_sha="b" * 40,
        ),
        enforcement=EnforcementService().decide(report),
    )


def _commit(message: str, index: int, author: Identity = HUMAN) -> Commit:
    return Commit(sha=f"{index:040x}", author=author, committer=HUMAN, message=message)


def test_clean_scan_is_success() -> None:
    conclusion, output = completed_output(_result([_commit("feat: clean\n", 1)]))
    assert conclusion is CheckRunConclusion.SUCCESS
    assert "PASS" in output.summary
    assert "All configured CommitGuard policies passed." in output.summary
    assert "| Commits scanned | 1 |" in output.summary


def test_block_is_failure_with_details_and_remediation() -> None:
    ai = "feat: add service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    conclusion, output = completed_output(_result([_commit(ai, 1), _commit("fix: clean\n", 2)]))
    assert conclusion is CheckRunConclusion.FAILURE
    assert output.title.startswith("Blocked: 1 violation")
    assert "BLOCKED" in output.summary
    assert "| Violations | 1 |" in output.summary
    text = output.text or ""
    assert "**Rule:** ai\\_coauthor" in text or "**Rule:** ai_coauthor" in text
    assert "Claude &lt;noreply@anthropic\\.com&gt;" in text
    assert "**Action:** block" in text
    assert "Remediation" in text


def test_warning_only_is_success_with_warnings() -> None:
    bot = Identity(
        name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"
    )
    conclusion, output = completed_output(_result([_commit("chore: bump\n", 1, author=bot)]))
    assert conclusion is CheckRunConclusion.SUCCESS
    assert "warning" in output.title
    assert "### Warnings" in (output.text or "")


def test_output_is_bounded_and_escaped() -> None:
    ai = "x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    commits = [
        _commit(f"[link](https://evil.example) <img src=x> {i}\n" + ai, i + 1) for i in range(60)
    ]
    result = _result(commits, weakenings=("ai_coauthor: block -> allow",))
    _, output = completed_output(result)
    text = output.text or ""
    assert text.count("**Rule:**") == MAX_CHECK_FINDINGS
    assert f"Showing {MAX_CHECK_FINDINGS} of 60 findings" in text
    assert "<img" not in text
    assert "](https" not in text
    assert "Security policy modification detected" in output.summary
    assert "<b>" not in output.summary
    payload = output.to_api()
    assert len(payload["summary"]) <= MAX_CHECK_TEXT_CHARS
    huge = CheckRunOutput(title="t" * 500, summary="s" * 100_000).to_api()
    assert len(huge["title"]) == 200
    assert len(huge["summary"]) <= MAX_CHECK_TEXT_CHARS


@pytest.mark.parametrize(
    ("kind", "conclusion"),
    [
        (FailureKind.CONFIGURATION, CheckRunConclusion.FAILURE),
        (FailureKind.INFRASTRUCTURE, CheckRunConclusion.FAILURE),
        (FailureKind.AUTHORIZATION, CheckRunConclusion.FAILURE),
        (FailureKind.INTERNAL, CheckRunConclusion.FAILURE),
        (FailureKind.TIMEOUT, CheckRunConclusion.TIMED_OUT),
    ],
)
def test_errors_never_succeed(kind: FailureKind, conclusion: CheckRunConclusion) -> None:
    got, output = error_output(kind, "GitHub API unavailable")
    assert got is conclusion
    assert "could not verify" in output.summary
    decision = EnforcementService.failed(kind, "x")
    assert decision.state is EnforcementState.ERROR
    assert not decision.allowed
    assert decision.exit_code == 2
    assert decision.check_conclusion != "success"


def test_enforcement_mapping_matches_cli_contract() -> None:
    service = EnforcementService()
    clean = _result([_commit("ok\n", 1)]).report
    blocked = _result([_commit("x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n", 1)]).report
    assert (service.decide(clean).exit_code, service.decide(clean).check_conclusion) == (
        0,
        "success",
    )
    assert (service.decide(blocked).exit_code, service.decide(blocked).check_conclusion) == (
        1,
        "failure",
    )
    with pytest.raises(ValueError, match="fail_on"):
        EnforcementService(Action.ALLOW)


def test_check_run_payload_requires_conclusion_exactly_when_completed() -> None:
    output = CheckRunOutput(title="t", summary="s")
    with pytest.raises(ValueError, match="conclusion"):
        check_run_update_payload(CheckRunStatus.COMPLETED, output)
    with pytest.raises(ValueError, match="conclusion"):
        check_run_update_payload(CheckRunStatus.IN_PROGRESS, output, CheckRunConclusion.SUCCESS)
    payload = check_run_update_payload(CheckRunStatus.COMPLETED, output, CheckRunConclusion.FAILURE)
    assert payload["conclusion"] == "failure"
    assert payload["status"] == "completed"


def test_least_privilege_permissions() -> None:
    assert dict(REQUIRED_PERMISSIONS) == {
        "checks": "write",
        "contents": "read",
        "metadata": "read",
        "pull_requests": "read",
    }
    assert [k for k, v in REQUIRED_PERMISSIONS.items() if v != "read"] == ["checks"]
    granted = {
        "checks": "write",
        "contents": "write",
        "metadata": "read",
        "administration": "write",
    }
    assert missing_permissions(granted) == {"pull_requests": "read"}
    assert excessive_permissions(granted) == {"administration": "write", "contents": "write"}


def test_ci_context_for_plan_is_valid() -> None:
    CIContext(
        provider=CIProvider.GITHUB, event=CIEventKind.PUSH, event_name="push", after_sha="b" * 40
    )


def test_app_refuses_git_without_lazy_fetch_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    from commitguard.exceptions.service import InfrastructureError
    from commitguard.github.repositories import require_mirror_git

    monkeypatch.setattr("commitguard.github.repositories.git_version", lambda: (2, 44, 9))
    with pytest.raises(InfrastructureError, match=r"Git 2\.45 or newer \(found 2\.44\.9\)"):
        require_mirror_git()
    monkeypatch.setattr("commitguard.github.repositories.git_version", lambda: (2, 45, 0))
    require_mirror_git()
