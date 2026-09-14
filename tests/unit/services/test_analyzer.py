"""End-to-end (in-memory): commit -> detectors -> findings -> policy -> decision."""

import pytest

from commitguard.config.loader import parse_config
from commitguard.core.decision import Action
from commitguard.policies.loader import build_policy_set
from commitguard.provenance.author import Identity
from commitguard.services.analysis import Analyzer

BOT = Identity(name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com")


@pytest.fixture
def analyzer(rules) -> Analyzer:  # type: ignore[no-untyped-def]
    return Analyzer.create(build_policy_set(), rules)


def test_fixture_decisions(analyzer: Analyzer, commit_case) -> None:  # type: ignore[no-untyped-def]
    report = analyzer.analyze(commit_case.commit)
    assert {f.finding.rule_id for f in report.findings} == commit_case.expected_rules
    assert report.failures == ()


@pytest.mark.parametrize(
    "message",
    [
        "feat: implement authentication\n",
        "feat: implement authentication\n\nCo-authored-by: John Doe <john@example.com>\n",
        (
            "feat: x\n\nCo-authored-by: Grace Hopper <grace@example.com>\n"
            "Co-authored-by: Alan Turing <alan@example.com>\n"
        ),
        "feat: use AI service for recommendations\n",
    ],
)
def test_human_commits_are_allowed(analyzer: Analyzer, make_commit, message: str) -> None:  # type: ignore[no-untyped-def]
    report = analyzer.analyze(make_commit(message))
    assert report.action is Action.ALLOW
    assert report.findings == ()


def test_documented_end_to_end_example(analyzer: Analyzer, make_commit) -> None:  # type: ignore[no-untyped-def]
    message = (
        "feat: add payment service\n\n"
        "Co-authored-by: John Doe <john@example.com>\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    report = analyzer.analyze(make_commit(message, sha="4f71c92" + "0" * 33))
    assert report.action is Action.BLOCK
    (item,) = report.findings
    assert item.finding.rule_id == "ai_coauthor"
    assert item.action is Action.BLOCK
    assert item.policy_id == "ai_coauthor"
    assert report.short_sha == "4f71c92"


def test_bot_only_commit_warns(analyzer: Analyzer, make_commit) -> None:  # type: ignore[no-untyped-def]
    report = analyzer.analyze(make_commit("build(deps): bump\n", author=BOT))
    assert report.action is Action.WARN


def test_ai_and_bot_blocks(analyzer: Analyzer, make_commit) -> None:  # type: ignore[no-untyped-def]
    commit = make_commit("chore\n\nCo-authored-by: Claude <noreply@anthropic.com>\n", author=BOT)
    assert analyzer.analyze(commit).action is Action.BLOCK


def test_policy_configuration_changes_the_decision_not_the_findings(rules, make_commit) -> None:  # type: ignore[no-untyped-def]
    commit = make_commit("feat\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    allow = parse_config("version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n")
    report = Analyzer.create(build_policy_set(allow), rules).analyze(commit)
    assert report.action is Action.ALLOW
    assert [f.action for f in report.findings] == [Action.ALLOW]


def test_disabled_detectors_are_skipped(rules, make_commit) -> None:  # type: ignore[no-untyped-def]
    config = parse_config("version: 1\npolicies:\n  bot_identity:\n    enabled: false\n")
    report = Analyzer.create(build_policy_set(config), rules).analyze(make_commit(author=BOT))
    assert "bot" in report.detectors_skipped
    assert report.action is Action.ALLOW


def test_trailer_flood_blocks_via_detector_failure(analyzer: Analyzer, make_commit) -> None:  # type: ignore[no-untyped-def]
    message = (
        "x\n\n"
        + "Signed-off-by: A <a@b.io>\n" * 2000
        + "Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    report = analyzer.analyze(make_commit(message))
    assert report.action is Action.BLOCK
    assert {f.failure.error_type for f in report.failures} == {"DetectionError"}


def test_report_serialises_for_json_and_audit(analyzer: Analyzer, make_commit) -> None:  # type: ignore[no-untyped-def]
    report = analyzer.analyze(make_commit("x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"))
    data = report.model_dump(mode="json")
    finding = data["findings"][0]
    assert finding["fingerprint"] == report.findings[0].finding.fingerprint
    assert finding["finding"]["evidence"][0]["value"] == "Claude <noreply@anthropic.com>"
    assert "message" not in data  # full commit messages are not part of reports
