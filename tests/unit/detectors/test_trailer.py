"""TrailerDetector: configured attribution trailers, footers, malformed trailers."""

import pytest

from commitguard.core.result import EvidenceSource, MatchKind
from commitguard.detectors.trailer import TrailerDetector


@pytest.fixture
def detector(rules):  # type: ignore[no-untyped-def]
    return TrailerDetector(rules)


def rules_for(detector, run_detector, make_commit, message: str) -> list[str]:  # type: ignore[no-untyped-def]
    return [f.rule_id for f in run_detector(detector, make_commit(message))]


def test_matches_fixture_expectations(detector, run_detector, commit_case) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, commit_case.commit)
    assert {f.rule_id for f in findings} == commit_case.expected_rules & detector.rules


@pytest.mark.parametrize(
    "trailer",
    [
        "Generated-by: Claude Code",
        "AI-generated-by: some internal tool",
        "AI-Assisted: yes",
        "Assisted-by: GitHub Copilot",
        "Reviewed-by: Claude <noreply@anthropic.com>",
        "Signed-off-by: Cursor Agent <cursoragent@cursor.com>",
    ],
)
def test_ai_attribution_trailers(detector, run_detector, make_commit, trailer: str) -> None:  # type: ignore[no-untyped-def]
    assert rules_for(detector, run_detector, make_commit, f"feat: x\n\n{trailer}\n") == [
        "ai_trailer"
    ]


@pytest.mark.parametrize(
    "trailer",
    [
        "Generated-by: protoc 3.21",
        "Reviewed-by: Grace Hopper <grace@example.com>",
        "Signed-off-by: John Doe <john@example.com>",
        "AI-Assisted: no",
        "AI-generated: false",
        "Fixes: #123",
        "Co-authored-by: Claude <noreply@anthropic.com>",  # coauthor detector's concern
    ],
)
def test_non_violations(detector, run_detector, make_commit, trailer: str) -> None:  # type: ignore[no-untyped-def]
    assert "ai_trailer" not in rules_for(
        detector, run_detector, make_commit, f"feat: x\n\n{trailer}\n"
    )


@pytest.mark.parametrize(
    "message",
    [
        "feat: use AI service for recommendations\n",
        "feat: add Claude API client\n\nWe call ChatGPT and Gemini from the backend.\n",
        "docs: mention Generated with Claude Code in the FAQ\n",
    ],
)
def test_wording_is_not_evidence(detector, run_detector, make_commit, message: str) -> None:  # type: ignore[no-untyped-def]
    assert rules_for(detector, run_detector, make_commit, message) == []


@pytest.mark.parametrize(
    "footer",
    [
        "🤖 Generated with [Claude Code](https://claude.com/claude-code)",
        "Generated with [Claude Code](https://claude.ai/code)",
        "  generated with claude code  ",
    ],
)
def test_exact_tool_footer(detector, run_detector, make_commit, footer: str) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(f"feat: x\n\n{footer}\n"))
    assert finding.rule_id == "ai_trailer"
    assert finding.evidence[0].source is EvidenceSource.MESSAGE
    assert finding.evidence[0].matched[0].kind is MatchKind.MESSAGE_MARKER


@pytest.mark.parametrize(
    "line",
    [
        "Co-authored-by:",
        "Co-authored-by: John",
        "Co-authored-by: <invalid>",
        "Co-authored-by: John <>",
        "Co-authored-by John <john@example.com>",
        "Signed-off-by John <john@example.com>",
    ],
)
def test_malformed_trailers(detector, run_detector, make_commit, line: str) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(f"feat: x\n\n{line}\n"))
    assert finding.rule_id == "malformed_trailer"
    assert finding.evidence[0].notes


def test_signed_off_without_email_is_not_malformed(detector, run_detector, make_commit) -> None:  # type: ignore[no-untyped-def]
    # require_identity is false for sign-offs: only structural problems count.
    assert rules_for(detector, run_detector, make_commit, "feat: x\n\nSigned-off-by: John\n") == []


def test_well_formed_human_trailers_are_clean(detector, run_detector, make_commit) -> None:  # type: ignore[no-untyped-def]
    message = (
        "feat: x\n\n"
        "Co-authored-by: John Doe <john@example.com>\n"
        "Signed-off-by: Ada <ada@example.com>\n"
    )
    assert rules_for(detector, run_detector, make_commit, message) == []
