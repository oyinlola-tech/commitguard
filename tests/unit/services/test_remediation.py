"""Auto-removal decides whether deleting message lines can clear a block.

The rule that matters: it must answer "no" for anything it cannot provably fix,
because the caller turns "yes" into a commit that Git would otherwise refuse.
"""

from datetime import UTC, datetime

import pytest

from commitguard.core.decision import Action
from commitguard.git.commit import Commit
from commitguard.policies.defaults import default_policy_set
from commitguard.provenance.author import Identity
from commitguard.services.analysis import Analyzer
from commitguard.services.remediation import removable_lines, strip_lines, with_actual_text

NOW = datetime(2026, 9, 18, tzinfo=UTC)
HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")
AGENT = Identity(name="Claude", email="noreply@anthropic.com")
ANALYZER = Analyzer.create(default_policy_set())


def report(message: str, *, author: Identity = HUMAN, committer: Identity | None = None):  # type: ignore[no-untyped-def]
    commit = Commit(
        sha="a" * 40,
        message=message,
        author=author,
        committer=committer or author,
        authored_at=NOW,
        committed_at=NOW,
    )
    return ANALYZER.analyze(commit)


# --------------------------------------------------------------------------- #
# What can be removed
# --------------------------------------------------------------------------- #
def test_an_ai_coauthor_trailer_is_removable() -> None:
    removals = removable_lines(report("feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>"))
    assert removals is not None
    assert [r.line_number for r in removals] == [3]
    assert removals[0].rule_id == "ai_coauthor"


def test_a_tool_footer_is_removable() -> None:
    removals = removable_lines(report("feat: x\n\nGenerated-with: Claude Code"))
    assert removals is not None
    assert [r.line_number for r in removals] == [3]


def test_several_ai_trailers_are_all_removable_and_reported_in_order() -> None:
    removals = removable_lines(
        report(
            "feat: x\n\n"
            "Co-authored-by: Ada Lovelace <ada@example.com>\n"
            "Co-authored-by: Claude <noreply@anthropic.com>\n"
            "Co-authored-by: ChatGPT <noreply@openai.com>"
        )
    )
    assert removals is not None
    assert [r.line_number for r in removals] == [4, 5]


# --------------------------------------------------------------------------- #
# What must NOT be removable: editing text cannot fix these
# --------------------------------------------------------------------------- #
def test_an_ai_author_identity_is_not_removable() -> None:
    assert removable_lines(report("feat: x", author=AGENT)) is None


def test_an_ai_committer_identity_is_not_removable() -> None:
    assert removable_lines(report("feat: x", committer=AGENT)) is None


def test_an_identity_finding_poisons_an_otherwise_removable_message() -> None:
    """One unfixable block means the whole commit stays blocked."""
    assert (
        removable_lines(
            report("feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>", author=AGENT)
        )
        is None
    )


def test_a_clean_commit_has_nothing_to_remove() -> None:
    clean = report("feat: x\n\nCo-authored-by: Ada Lovelace <ada@example.com>")
    assert clean.action is Action.ALLOW
    assert removable_lines(clean) is None


def test_a_warning_only_commit_has_nothing_to_remove() -> None:
    """Warnings do not block, so nothing has to be deleted to let the commit through."""
    warned = report("feat: x", author=Identity(name="dependabot[bot]", email="bot@github.com"))
    assert warned.action is not Action.BLOCK
    assert removable_lines(warned) is None


# --------------------------------------------------------------------------- #
# Stripping
# --------------------------------------------------------------------------- #
def test_stripping_removes_only_the_named_lines() -> None:
    message = "feat: x\n\nbody\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    assert strip_lines(message, frozenset({5})) == "feat: x\n\nbody\n"


def test_stripping_trims_blank_lines_left_at_the_end() -> None:
    message = "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    assert strip_lines(message, frozenset({3})) == "feat: x\n"


def test_stripping_keeps_a_human_trailer_beside_a_removed_one() -> None:
    message = (
        "feat: x\n\n"
        "Co-authored-by: Ada Lovelace <ada@example.com>\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    assert strip_lines(message, frozenset({4})) == (
        "feat: x\n\nCo-authored-by: Ada Lovelace <ada@example.com>\n"
    )


def test_stripping_everything_yields_an_empty_message() -> None:
    """The caller must refuse this rather than commit an empty message."""
    assert strip_lines("Co-authored-by: Claude <noreply@anthropic.com>\n", frozenset({1})) == ""


@pytest.mark.parametrize("line", [0, 99])
def test_stripping_ignores_line_numbers_that_do_not_exist(line: int) -> None:
    assert strip_lines("feat: x\n", frozenset({line})) == "feat: x\n"


def test_removed_lines_report_the_whole_line_not_just_the_match() -> None:
    message = "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>"
    removals = removable_lines(report(message))
    assert removals is not None
    assert removals[0].text == "Claude <noreply@anthropic.com>"
    detailed = with_actual_text(message, removals)
    assert detailed[0].text == "Co-authored-by: Claude <noreply@anthropic.com>"
