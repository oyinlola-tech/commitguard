"""IdentityDetector: AI agents as author or committer."""

import pytest

from commitguard.core.result import EvidenceSource
from commitguard.detectors.identity import IdentityDetector
from commitguard.provenance.author import Identity

HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")
CLAUDE = Identity(name="Claude", email="noreply@anthropic.com")


@pytest.fixture
def detector(rules):  # type: ignore[no-untyped-def]
    return IdentityDetector(rules)


def test_matches_fixture_expectations(detector, run_detector, commit_case) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, commit_case.commit)
    assert {f.rule_id for f in findings} == commit_case.expected_rules & detector.rules


def test_ai_author(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(author=CLAUDE, committer=HUMAN))
    assert finding.rule_id == "ai_identity"
    assert [e.source for e in finding.evidence] == [EvidenceSource.AUTHOR]
    assert finding.evidence[0].value == "Claude <noreply@anthropic.com>"


def test_ai_committer(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(author=HUMAN, committer=CLAUDE))
    assert [e.source for e in finding.evidence] == [EvidenceSource.COMMITTER]


def test_same_agent_as_author_and_committer_is_one_finding(
    detector, make_commit, run_detector
) -> None:  # type: ignore[no-untyped-def]
    (finding,) = run_detector(detector, make_commit(author=CLAUDE, committer=CLAUDE))
    assert [e.source for e in finding.evidence] == [EvidenceSource.AUTHOR, EvidenceSource.COMMITTER]


@pytest.mark.parametrize(
    "identity",
    [
        HUMAN,
        Identity(name="Jane Doe", email="jane@anthropic.com"),
        Identity(name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"),
        Identity(
            name="github-actions[bot]",
            email="41898282+github-actions[bot]@users.noreply.github.com",
        ),
        Identity(name="", email=""),
    ],
)
def test_humans_and_bots_are_not_ai(
    detector, make_commit, run_detector, identity: Identity
) -> None:  # type: ignore[no-untyped-def]
    assert run_detector(detector, make_commit(author=identity)) == []


def test_trailers_are_not_this_detectors_concern(detector, make_commit, run_detector) -> None:  # type: ignore[no-untyped-def]
    message = "feat\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    assert run_detector(detector, make_commit(message)) == []
