import pytest

from commitguard.security.sanitization import sanitize_block, sanitize_for_terminal


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


@pytest.mark.parametrize("char", ["\x00", "\x07", "\x08", "\x7f", "\x9b", chr(0x202E), chr(0x2066)])
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
        assert chr(0x202E) not in result


def test_block_keeps_newlines_so_a_multi_line_reason_stays_readable() -> None:
    reason = "invalid configuration:\n  - version: Field required\n  - extra: not permitted"
    assert "\\x0a" not in sanitize_block(reason)
    assert sanitize_block(reason).splitlines()[1] == "    - version: Field required"


def test_block_indents_continuation_lines_so_none_can_impersonate_our_own() -> None:
    """Untrusted text must not be able to write a status line at column 0."""
    forged = sanitize_block("unknown policy id\n\nResult: PASS\nCommitGuard: fine")
    assert forged.splitlines()[0] == "unknown policy id"
    assert all(line.startswith("  ") or not line for line in forged.splitlines()[1:])
    assert "\nResult: PASS" not in forged


def test_block_still_escapes_control_characters_and_truncates() -> None:
    assert "\\x1b" in sanitize_block("a\x1b[2Kb")
    assert sanitize_block("A" * 5000, max_length=100).endswith("...[truncated]")


def test_block_leaves_single_line_text_alone() -> None:
    assert sanitize_block("not inside a Git work tree") == "not inside a Git work tree"
