"""BotDetector: configured automation identities (never AI findings)."""

import pytest

from commitguard.detectors.bot import BotDetector
from commitguard.detectors.coauthor import CoauthorDetector
from commitguard.detectors.identity import IdentityDetector
from commitguard.provenance.author import Identity

BOTS = [
    Identity(name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"),
    Identity(name="renovate[bot]", email="29139614+renovate[bot]@users.noreply.github.com"),
    Identity(
        name="github-actions[bot]", email="41898282+github-actions[bot]@users.noreply.github.com"
    ),
    Identity(name="release-bot", email="release@example.com"),
]


@pytest.fixture
def detector(rules):  # type: ignore[no-untyped-def]
    return BotDetector(rules)


def test_matches_fixture_expectations(detector, run_detector, commit_case) -> None:  # type: ignore[no-untyped-def]
    findings = run_detector(detector, commit_case.commit)
    assert {f.rule_id for f in findings} == commit_case.expected_rules & detector.rules


@pytest.mark.parametrize("bot", BOTS, ids=lambda b: b.name)
def test_bot_authors_are_bot_findings_not_ai(
    detector, rules, run_detector, make_commit, bot: Identity
) -> None:  # type: ignore[no-untyped-def]
    commit = make_commit("build(deps): bump x\n", author=bot)
    (finding,) = run_detector(detector, commit)
    assert finding.rule_id == "bot_identity"
    assert run_detector(IdentityDetector(rules), commit) == []


def test_bot_coauthor(detector, rules, run_detector, make_commit) -> None:  # type: ignore[no-untyped-def]
    commit = make_commit(
        "chore: x\n\n"
        "Co-authored-by: renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>\n"
    )
    (finding,) = run_detector(detector, commit)
    assert finding.rule_id == "bot_identity"
    assert run_detector(CoauthorDetector(rules), commit) == []


@pytest.mark.parametrize(
    "identity",
    [
        Identity(name="Ada Lovelace", email="ada@example.com"),
        Identity(
            name="some-tool[bot]", email="1+some-tool[bot]@users.noreply.github.com"
        ),  # not configured
        Identity(name="GitHub", email="noreply@github.com"),
        Identity(name="Copilot", email="175728472+Copilot@users.noreply.github.com"),  # AI, not bot
    ],
)
def test_unconfigured_identities_are_not_bots(
    detector, run_detector, make_commit, identity: Identity
) -> None:  # type: ignore[no-untyped-def]
    assert run_detector(detector, make_commit(author=identity)) == []
