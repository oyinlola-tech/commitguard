"""CoauthorDetector: AI agents in Co-authored-by trailers."""

import pytest

from commitguard.core.result import Confidence, EvidenceSource, MatchKind, Severity
from commitguard.detectors.base import Detector
from commitguard.detectors.coauthor import CoauthorDetector
from commitguard.exceptions.detection import DetectionError
from commitguard.provenance.author import Identity
from commitguard.provenance.trailers import MAX_TRAILERS

# Invisible/confusable characters are built from code points so none appear
# literally in this source file.
ZWSP = chr(0x200B)
RLO = chr(0x202E)


@pytest.fixture
def detector(rules):  # type: ignore[no-untyped-def]
    return CoauthorDetector(rules)


def coauthored(*identities: str) -> str:
    return "feat: implement authentication\n\n" + "".join(
        f"Co-authored-by: {identity}\n" for identity in identities
    )


def test_implements_detector_interface(detector) -> None:  # type: ignore[no-untyped-def]
    assert isinstance(detector, Detector)
    assert detector.name == "coauthor"
    assert detector.rules == {"ai_coauthor"}


def test_matches_fixture_expectations(detector, run_detector, commit_case) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, commit_case.commit)
    assert {f.rule_id for f in findings} == commit_case.expected_rules & detector.rules


def test_documented_example(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    sha = "8e71c2a" + "0" * 33
    (finding,) = run_detector(
        detector, make_commit(coauthored("Claude <noreply@anthropic.com>"), sha=sha)
    )
    assert finding.detector == "coauthor"
    assert finding.rule_id == "ai_coauthor"
    assert finding.severity is Severity.HIGH
    assert finding.confidence is Confidence.HIGH
    assert finding.title == "AI coauthor detected"
    assert finding.commit_sha == sha
    (evidence,) = finding.evidence
    assert evidence.value == "Claude <noreply@anthropic.com>"
    assert evidence.source is EvidenceSource.COAUTHOR_TRAILER
    assert evidence.line_number == 3
    assert {r.kind for r in evidence.matched} >= {MatchKind.NAME, MatchKind.EMAIL}
    assert "remove" in finding.remediation.lower()
    # Honest wording: attribution evidence, never "written by AI".
    assert "written by" not in finding.message.lower()


@pytest.mark.parametrize(
    "identity",
    [
        "Claude <noreply@anthropic.com>",
        "Claude Code <noreply@anthropic.com>",
        "Claude Opus 4.5 <noreply@anthropic.com>",
        "ChatGPT <chatgpt@example.com>",
        "OpenAI Codex <codex@example.com>",
        "GitHub Copilot <copilot@example.com>",
        "Copilot <175728472+Copilot@users.noreply.github.com>",
        "Cursor Agent <cursoragent@cursor.com>",
        "Gemini <gemini@example.com>",
        "Gemini Code Assist <gemini@example.com>",
        "Windsurf <windsurf@example.com>",
        "Cline <cline@example.com>",
        "Roo Code <roo@example.com>",
        "Amazon Q Developer <q@example.com>",
        "Codeium <codeium@example.com>",
        "Devin AI <devin@example.com>",
        "devin-ai-integration[bot] <158243242+devin-ai-integration[bot]@users.noreply.github.com>",
        "aider (gpt-4o) <aider@aider.chat>",
        "OpenHands <openhands@all-hands.dev>",
    ],
)
def test_known_ai_agents(detector, make_commit, run_detector, identity: str) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, make_commit(coauthored(identity)))
    assert [f.rule_id for f in findings] == ["ai_coauthor"]


@pytest.mark.parametrize("name", ["Claude", "CLAUDE", "claude", "cLaUdE", "  Claude  "])
def test_case_and_whitespace_variations(detector, make_commit, run_detector, name: str) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, make_commit(coauthored(f"{name} <someone@example.com>")))
    assert len(findings) == 1


@pytest.mark.parametrize(
    ("identity", "confidence"),
    [
        ("Some Agent <noreply@anthropic.com>", Confidence.HIGH),  # known AI email
        ("Assistant <no-reply@cursor.com>", Confidence.HIGH),  # automation address at AI domain
        ("Claude <someone@example.com>", Confidence.MEDIUM),  # AI name, unknown email
        ("Claude <jane@anthropic.com>", Confidence.HIGH),  # AI name + vendor domain
    ],
)
def test_email_variations_that_match(
    detector, make_commit, run_detector, identity: str, confidence: Confidence
) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(coauthored(identity)))
    assert finding.confidence is confidence


@pytest.mark.parametrize(
    "identity",
    [
        "John Doe <john@example.com>",
        "Jane Doe <jane@anthropic.com>",  # human at a known AI vendor domain
        "Sam Altman <sam@openai.com>",
        "Claudette <claudette@example.com>",  # not "Claude"
        "Claude Dupont <claude.dupont@example.fr>",  # a human named Claude
        "Devin Smith <devin@example.com>",
        "Devin <devin@example.com>",  # ambiguous alias without vendor evidence
        "Copilot Fan <fan@example.com>",
        "Nimbus AI Agent <agent@nimbus-ai.example>",  # unknown: not guessed
    ],
)
def test_false_positives_are_avoided(detector, make_commit, run_detector, identity: str) -> None:  # type: ignore[no-untyped-def]
    assert run_detector(detector, make_commit(coauthored(identity))) == []


def test_only_the_ai_coauthor_is_reported(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    message = coauthored(
        "Grace Hopper <grace@example.com>",
        "Alan Turing <alan@example.com>",
        "Claude <noreply@anthropic.com>",
        "John Doe <john@example.com>",
    )
    (finding,) = run_detector(detector, make_commit(message))
    assert finding.evidence[0].value == "Claude <noreply@anthropic.com>"
    assert finding.evidence[0].line_number == 5


def test_multiple_ai_coauthors_each_reported(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    message = coauthored("Claude <noreply@anthropic.com>", "Cursor Agent <cursoragent@cursor.com>")
    findings = run_detector(detector, make_commit(message))
    assert [f.evidence[0].line_number for f in findings] == [3, 4]


@pytest.mark.parametrize(
    "line",
    [
        "Co-authored-by:",
        "Co-authored-by: Claude",
        "Co-authored-by: <invalid>",
        "Co-authored-by: Claude <>",
        "Co-authored-by: <<>>",
        "Co-authored-by: Claude <noreply@anthropic.com",
        "Co-authored-by: \x00\x1b[31m",
    ],
)
def test_malformed_trailers_do_not_crash(detector, make_commit, run_detector, line: str) -> None:  # type: ignore[no-untyped-def]
    run_detector(detector, make_commit(f"feat: x\n\n{line}\n"))


def test_malformed_ai_trailer_still_detected(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    for message in (
        "feat: x\n\nCo-authored-by: Claude\n",
        "feat: x\n\nCO-AUTHORED-BY Claude noreply@anthropic.com\n",
        "feat: x\n\nCo authored by: Claude <noreply@anthropic.com>\n",
        f"feat: x\n\nCo-authored{ZWSP}-by: Claude <noreply@anthropic.com>\n",
    ):
        findings = run_detector(detector, make_commit(message))
        assert len(findings) == 1, message
        assert findings[0].evidence[0].notes


@pytest.mark.parametrize(
    "identity",
    [
        "Cl" + chr(0x0430) + "ude <someone@example.com>",  # Cyrillic a
        "\U0001d402\U0001d425\U0001d41a\U0001d42e\U0001d41d\U0001d41e <x@example.com>",  # bold
        f"Cl{ZWSP}aude <x@example.com>",  # zero-width space
        f"{RLO}Claude <x@example.com>",  # bidi override
    ],
)
def test_unicode_evasion(detector, make_commit, run_detector, identity: str) -> None:  # type: ignore[no-untyped-def]
    assert len(run_detector(detector, make_commit(coauthored(identity)))) == 1


def test_trailer_outside_trailer_block_is_still_attribution(
    detector, make_commit, run_detector
) -> None:  # type: ignore[no-untyped-def]
    message = "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n\nMore text.\n"
    (finding,) = run_detector(detector, make_commit(message))
    assert "outside the commit's trailer block" in finding.evidence[0].notes


def test_trailer_flood_fails_closed(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    flood = "Signed-off-by: A <a@example.com>\n" * (MAX_TRAILERS + 1)
    message = f"feat: x\n\n{flood}Co-authored-by: Claude <noreply@anthropic.com>\n"
    with pytest.raises(DetectionError):
        run_detector(detector, make_commit(message))


def test_does_not_inspect_author(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    claude = Identity(name="Claude", email="noreply@anthropic.com")
    assert run_detector(detector, make_commit("feat: x\n", author=claude)) == []
