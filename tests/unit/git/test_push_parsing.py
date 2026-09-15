import pytest

from commitguard.exceptions.base import UnsafeInputError
from commitguard.git.push import parse_pre_push_input

A = "a" * 40
B = "b" * 40
Z = "0" * 40


def test_parses_multiple_updates() -> None:
    text = (
        f"refs/heads/main {A} refs/heads/main {B}\n"
        f"refs/tags/v1 {B} refs/tags/v1 {Z}\n"
        f"(delete) {Z} refs/heads/old {A}\n"
    )
    main, tag, delete = parse_pre_push_input(text)
    assert not main.is_new_ref
    assert not main.is_delete
    assert tag.is_tag
    assert tag.is_new_ref
    assert delete.is_delete


def test_empty_input() -> None:
    assert parse_pre_push_input("") == ()
    assert parse_pre_push_input("\n\n") == ()


def test_unusual_but_valid_ref_names_are_kept_verbatim() -> None:
    ref = 'refs/heads/we"ird;$(id)&&`x`|' + chr(0x00E9)
    (update,) = parse_pre_push_input(f"{ref} {A} {ref} {Z}\n")
    assert update.local_ref == ref


def test_sha256_repositories() -> None:
    (update,) = parse_pre_push_input(f"refs/heads/main {'c' * 64} refs/heads/main {'0' * 64}\n")
    assert update.is_new_ref


def test_crlf_line_endings() -> None:
    assert len(parse_pre_push_input(f"refs/heads/main {A} refs/heads/main {B}\r\n")) == 1


@pytest.mark.parametrize(
    "line",
    [
        f"refs/heads/main {A} refs/heads/main",  # 3 fields
        f"refs/heads/main {A} refs/heads/main {B} extra",
        f"refs/heads/main HEAD refs/heads/main {B}",  # not an object id
        f"refs/heads/main {A[:-1]}G refs/heads/main {B}",
        f"refs/heads/main {A} refs/heads/main {'0' * 64}",  # mixed formats
        f"refs/heads/ma\x1bin {A} refs/heads/main {B}",  # control character
        f"refs/heads/main {Z} refs/heads/main {B}",  # zero local oid without (delete)
        f"(delete) {A} refs/heads/main {B}",
        f"--upload-pack=evil {A} refs/heads/main {B}".replace(" ", "\t", 1),
    ],
)
def test_malformed_input_is_rejected(line: str) -> None:
    with pytest.raises(UnsafeInputError):
        parse_pre_push_input(line + "\n")
