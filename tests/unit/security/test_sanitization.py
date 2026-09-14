import pytest

from commitguard.security.sanitization import sanitize_for_terminal


def test_plain_text_is_unchanged() -> None:
    assert sanitize_for_terminal("Claude <noreply@anthropic.com>") == (
        "Claude <noreply@anthropic.com>"
    )


def test_ansi_line_erase_is_neutralised() -> None:
    hostile = "Co-authored-by: Claude <noreply@anthropic.com>\x1b[1A\x1b[2K\r"
    result = sanitize_for_terminal(hostile)
    assert "\x1b" not in result
    assert "\r" not in result
    assert "\\x1b[1A" in result
    assert "Co-authored-by: Claude" in result


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x08", "\x7f", "\x9b", "‮", "⁦"])
def test_control_and_bidi_characters_are_escaped(char: str) -> None:
    result = sanitize_for_terminal(f"a{char}b")
    assert char not in result
    assert result.startswith("a\\")
    assert result.endswith("b")


def test_newlines() -> None:
    assert sanitize_for_terminal("a\nb") == "a\\x0ab"
    assert sanitize_for_terminal("a\nb", keep_newlines=True) == "a\nb"


def test_truncation() -> None:
    result = sanitize_for_terminal("A" * 5000, max_length=100)
    assert len(result) == 100
    assert result.endswith("...[truncated]")


def test_rejects_tiny_max_length() -> None:
    with pytest.raises(ValueError, match="max_length"):
        sanitize_for_terminal("x", max_length=1)


def test_malicious_fixture_is_fully_neutralised(commit_cases) -> None:  # type: ignore[no-untyped-def]
    case = next(c for c in commit_cases if c.name == "malicious_message")
    for text in (case.commit.message, case.commit.author.name):
        result = sanitize_for_terminal(text, max_length=10_000)
        assert not any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in result)
        assert "‮" not in result
