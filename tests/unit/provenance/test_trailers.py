"""Trailer parser."""

import time

import pytest

from commitguard.provenance.author import IdentityIssue
from commitguard.provenance.trailers import MAX_TRAILERS, TrailerIssue, parse_trailers

ZWSP = chr(0x200B)
FULLWIDTH_COLON = chr(0xFF1A)


def only(message: str):  # type: ignore[no-untyped-def]
    (trailer,) = parse_trailers(message).trailers
    return trailer


def test_parses_coauthor_trailer() -> None:
    trailer = only(
        "feat: implement authentication\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    )
    assert trailer.key == "Co-authored-by"
    assert trailer.normalized_key == "co-authored-by"
    assert trailer.value == "Claude <noreply@anthropic.com>"
    assert trailer.name == "Claude"
    assert trailer.email == "noreply@anthropic.com"
    assert trailer.line_number == 3
    assert trailer.in_trailer_block
    assert trailer.well_formed
    assert trailer.identity.well_formed


def test_multiple_trailers_in_order() -> None:
    message = (
        "subject\n\n"
        "Co-authored-by: A <a@x.io>\nSigned-off-by: B <b@x.io>\nCo-authored-by: C <c@x.io>\n"
    )
    trailers = parse_trailers(message).trailers
    assert [(t.normalized_key, t.name) for t in trailers] == [
        ("co-authored-by", "A"),
        ("signed-off-by", "B"),
        ("co-authored-by", "C"),
    ]


def test_message_without_trailers() -> None:
    assert parse_trailers("fix: typo\n").trailers == ()
    assert parse_trailers("").trailers == ()


def test_conventional_commit_subject_is_not_a_trailer() -> None:
    assert parse_trailers("feat: implement authentication\n").trailers == ()


def test_urls_are_not_trailers() -> None:
    assert parse_trailers("fix\n\nSee https://example.com/issue\n").trailers == ()


@pytest.mark.parametrize(
    ("line", "name", "email", "issues"),
    [
        ("Co-authored-by:", None, None, {IdentityIssue.EMPTY}),
        ("Co-authored-by: Claude", "Claude", None, {IdentityIssue.MISSING_EMAIL}),
        (
            "Co-authored-by: <invalid>",
            None,
            "invalid",
            {IdentityIssue.MISSING_NAME, IdentityIssue.INVALID_EMAIL},
        ),
        ("Co-authored-by: Claude <>", "Claude", None, {IdentityIssue.MISSING_EMAIL}),
    ],
)
def test_malformed_values_are_preserved(line: str, name, email, issues) -> None:  # type: ignore[no-untyped-def]
    trailer = only(f"feat\n\n{line}\n")
    assert trailer.normalized_key == "co-authored-by"
    assert trailer.name == name
    assert trailer.email == email
    assert set(trailer.identity.issues) == issues


def test_empty_value_issue() -> None:
    assert TrailerIssue.EMPTY_VALUE in only("x\n\nCo-authored-by:\n").issues


@pytest.mark.parametrize(
    ("line", "issue"),
    [
        ("CO-AUTHORED-BY Claude noreply@anthropic.com", TrailerIssue.MISSING_SEPARATOR),
        ("Co-authored-by=Claude <noreply@anthropic.com>", TrailerIssue.MISSING_SEPARATOR),
        ("Co authored by: Claude <noreply@anthropic.com>", TrailerIssue.NONSTANDARD_KEY),
        ("Co_authored_by: Claude <noreply@anthropic.com>", TrailerIssue.NONSTANDARD_KEY),
        ("Co-authored-by : Claude <noreply@anthropic.com>", TrailerIssue.NONSTANDARD_KEY),
        (f"Co-authored{ZWSP}-by: Claude <noreply@anthropic.com>", TrailerIssue.NONSTANDARD_KEY),
    ],
)
def test_evasive_key_spellings(line: str, issue: TrailerIssue) -> None:
    trailer = only(f"feat\n\n{line}\n")
    assert trailer.normalized_key == "co-authored-by"
    assert issue in trailer.issues
    assert trailer.name == "Claude"


def test_fullwidth_colon_is_normalised() -> None:
    assert only(f"feat\n\nCo-authored-by{FULLWIDTH_COLON} Claude <a@b.io>\n").name == "Claude"


def test_trailer_outside_final_paragraph() -> None:
    trailer = only("feat\n\nCo-authored-by: Claude <a@b.io>\n\nclosing words\n")
    assert not trailer.in_trailer_block


def test_indented_trailer_is_not_folded_into_previous_value() -> None:
    trailers = parse_trailers(
        "x\n\nSigned-off-by: A <a@b.io>\n  Co-authored-by: Claude <c@d.io>\n"
    ).trailers
    assert [t.normalized_key for t in trailers] == ["signed-off-by", "co-authored-by"]


def test_continuation_lines_are_folded() -> None:
    trailer = only("x\n\nNote-by: first part\n  second part\n")
    assert trailer.value == "first part second part"


@pytest.mark.parametrize("separator", [chr(0x2028), "\r", "\x0b", "\x1c"])
def test_unicode_line_separators_cannot_hide_trailers(separator: str) -> None:
    trailers = parse_trailers(f"subject{separator}Co-authored-by: Claude <a@b.io>").trailers
    assert [t.name for t in trailers] == ["Claude"]


def test_escape_sequences_are_preserved_as_issues() -> None:
    trailer = only("x\n\nCo-authored-by: Claude <noreply@anthropic.com>\x1b[1A\x1b[2K\r\n")
    assert trailer.email == "noreply@anthropic.com"
    assert IdentityIssue.TRAILING_CONTENT in trailer.identity.issues


def test_trailer_limit_sets_truncated() -> None:
    message = "x\n\n" + "Signed-off-by: A <a@b.io>\n" * (MAX_TRAILERS + 5)
    parsed = parse_trailers(message)
    assert len(parsed.trailers) == MAX_TRAILERS
    assert parsed.truncated


def test_pathological_input_is_fast() -> None:
    inputs = [
        "x" * 2_000_000,
        ":" * 200_000,
        "Co-authored-by: " + "<" * 200_000,
        "a-by " * 200_000,
        "\n".join(["Co-authored-by: A <a@b.io>"] * 50_000),
        "x\n\nSigned-off-by: A\n" + "  continuation line\n" * 200_000,  # no quadratic folding
    ]
    start = time.perf_counter()
    for text in inputs:
        parse_trailers(text)
    # Generous budget: linear parsing takes ~2s on a loaded machine; quadratic takes minutes.
    assert time.perf_counter() - start < 20


@pytest.mark.parametrize(
    "prefix",
    ["�", "?", ">", "> ", "•", "* ", "- ", "#", "~~", "»", "!!!!"],
)
def test_leading_symbols_cannot_hide_a_trailer(prefix: str) -> None:
    """Regression: found by the detection benchmark (dataset 1.0.0, adversarial class).

    A replacement character from malformed UTF-8 before ``Co-authored-by`` made the
    parser ignore the line, so the AI co-author was allowed.
    """
    parsed = parse_trailers(f"feat: x\n\n{prefix}Co-authored-by: Claude <noreply@anthropic.com>\n")
    [trailer] = parsed.trailers
    assert trailer.normalized_key == "co-authored-by"
    assert trailer.email == "noreply@anthropic.com"
    assert TrailerIssue.LEADING_CHARACTERS in trailer.issues


def test_leading_character_skipping_is_bounded_and_ignores_prose() -> None:
    assert (
        parse_trailers("feat: x\n\n" + "!" * 17 + "Co-authored-by: Claude <a@b.c>\n").trailers == ()
    )
    assert parse_trailers("feat: x\n\n* see http://example.com: details\n").trailers == ()
    assert parse_trailers("feat: x\n\n-- \n").trailers == ()
