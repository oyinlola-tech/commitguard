import pytest

from commitguard.provenance.author import (
    IdentityIssue,
    github_login,
    is_plausible_email,
    parse_identity,
)
from commitguard.provenance.normalization import (
    normalize_email,
    normalize_name,
    normalize_trailer_key,
)


@pytest.mark.parametrize(
    ("value", "name", "email", "issues"),
    [
        ("Claude <noreply@anthropic.com>", "Claude", "noreply@anthropic.com", set()),
        ("  John   Doe   <john@example.com>  ", "John   Doe", "john@example.com", set()),
        ("Claude", "Claude", None, {IdentityIssue.MISSING_EMAIL}),
        (
            "Claude noreply@anthropic.com",
            "Claude",
            "noreply@anthropic.com",
            {IdentityIssue.MISSING_BRACKETS},
        ),
        ("<invalid>", None, "invalid", {IdentityIssue.MISSING_NAME, IdentityIssue.INVALID_EMAIL}),
        ("Claude <>", "Claude", None, {IdentityIssue.MISSING_EMAIL}),
        ("", None, None, {IdentityIssue.EMPTY}),
        ("Claude <a@b.io> extra", "Claude", "a@b.io", {IdentityIssue.TRAILING_CONTENT}),
        (
            "Claude <noreply@anthropic.com",
            "Claude",
            "noreply@anthropic.com",
            {IdentityIssue.UNBALANCED_BRACKETS},
        ),
        ("A > B <a@b.io>", "A > B", "a@b.io", {IdentityIssue.UNBALANCED_BRACKETS}),
    ],
)
def test_parse_identity(value: str, name, email, issues) -> None:  # type: ignore[no-untyped-def]
    parsed = parse_identity(value)
    assert parsed.name == name
    assert parsed.email == email
    assert set(parsed.issues) == issues


@pytest.mark.parametrize(
    ("email", "ok"),
    [
        ("a@b.io", True),
        ("invalid", False),
        ("a@b", False),
        ("@b.io", False),
        ("a b@c.io", False),
        ("a@@b.io", False),
    ],
)
def test_plausible_email(email: str, ok: bool) -> None:
    assert is_plausible_email(email) is ok


@pytest.mark.parametrize(
    ("email", "login"),
    [
        ("175728472+Copilot@users.noreply.github.com", "copilot"),
        ("octocat@users.noreply.github.com", "octocat"),
        ("49699333+dependabot[bot]@users.noreply.github.com", "dependabot[bot]"),
        ("x+y@users.noreply.github.com", None),
        ("copilot@github.com", None),
    ],
)
def test_github_login(email: str, login: str | None) -> None:
    assert github_login(email) == login


def test_normalisation() -> None:
    assert normalize_name("  cLaUdE\t Code ") == "claude code"
    assert normalize_name("Cl" + chr(0x0430) + "ude") == "claude"
    assert normalize_name("".join(chr(0xFF00 + ord(c) - 0x20) for c in "Claude")) == "claude"
    assert normalize_name("Claudette") != normalize_name("Claude")
    assert normalize_email(" NoReply@Anthropic.COM. ") == "noreply@anthropic.com"
    assert normalize_trailer_key("Co_Authored  By") == "co-authored-by"
    assert normalize_trailer_key("Co-authored -by") == "co-authored-by"
