import sys
from pathlib import Path

import pytest

from commitguard.exceptions.base import UnsafeInputError
from commitguard.utils.filesystem import atomic_write_text, read_text_limited
from commitguard.utils.subprocess import run_command


def test_shell_strings_are_refused() -> None:
    with pytest.raises(UnsafeInputError, match="sequence"):
        run_command("echo hello")


@pytest.mark.parametrize("argv", [[], [sys.executable, "-c", "print(1)\x00"], [sys.executable, 1]])
def test_malformed_argv_is_refused(argv: list[object]) -> None:
    with pytest.raises(UnsafeInputError):
        run_command(argv)  # type: ignore[arg-type]


def test_metacharacters_are_passed_literally() -> None:
    payload = "$(echo pwned); `id` | && >"
    result = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", payload])
    assert result.ok
    assert result.stdout.decode().strip() == payload


def test_stdin_is_closed_by_default() -> None:
    result = run_command([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"])
    assert result.stdout.decode().strip() == "''"


def test_atomic_write_never_overwrites_by_default(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    atomic_write_text(target, "first")
    with pytest.raises(FileExistsError):
        atomic_write_text(target, "second")
    assert target.read_text() == "first"
    assert list(tmp_path.iterdir()) == [target]  # no temp files left behind
    atomic_write_text(target, "second", overwrite=True)
    assert target.read_text() == "second"


def test_read_text_limited(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"\xff\xfe")
    with pytest.raises(UnsafeInputError, match="not valid"):
        read_text_limited(target, max_bytes=10)
