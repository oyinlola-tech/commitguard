"""Trailer and identity parsing: Phase 2 specification (strict xfail)."""

import pytest

from commitguard.provenance.author import Identity, parse_identity
from commitguard.provenance.trailers import parse_trailers

pytestmark = [
    pytest.mark.phase2,
    pytest.mark.xfail(raises=NotImplementedError, strict=True, reason="Phase 2"),
]


def test_parses_coauthor_trailer() -> None:
    message = "feat: implement authentication\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    (trailer,) = parse_trailers(message)
    assert trailer.normalized_key == "co-authored-by"
    assert trailer.value == "Claude <noreply@anthropic.com>"
    assert trailer.line_number == 3


def test_parses_multiple_trailers_in_order() -> None:
    message = "subject\n\nCo-authored-by: A <a@x>\nSigned-off-by: B <b@x>\n"
    assert [t.normalized_key for t in parse_trailers(message)] == [
        "co-authored-by",
        "signed-off-by",
    ]


def test_message_without_trailers() -> None:
    assert parse_trailers("fix: typo\n") == ()


def test_parse_identity() -> None:
    assert parse_identity("Claude <noreply@anthropic.com>") == Identity(
        name="Claude", email="noreply@anthropic.com"
    )
