"""pre-commit and commit-msg enforcement through real `git commit`."""

import sys
from pathlib import Path

import pytest

AI_MSG = "feat: add payments\n\nCo-authored-by: Claude <noreply@anthropic.com>"


def no_commit_created(repo) -> bool:  # type: ignore[no-untyped-def]
    return repo.run("rev-parse", "--verify", "--quiet", "HEAD").returncode != 0


def test_clean_commit_succeeds(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    result = commit_with_hooks(
        "feat: implement authentication\n\nCo-authored-by: John Doe <john@example.com>"
    )
    assert result.returncode == 0, result.stderr
    assert "BLOCKED" not in result.stderr


def test_ai_coauthor_commit_is_blocked(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    result = commit_with_hooks(AI_MSG)
    assert result.returncode == 1
    assert "COMMIT BLOCKED (commit-msg)" in result.stderr
    assert "ai_coauthor" in result.stderr
    assert "Nothing was committed" in result.stderr
    assert no_commit_created(hooked_repo)


def test_ai_author_is_blocked_before_the_message_stage(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    result = commit_with_hooks("clean", "--author=Claude <noreply@anthropic.com>")
    assert result.returncode == 1
    assert "COMMIT BLOCKED (pre-commit)" in result.stderr
    assert "ai_identity" in result.stderr
    assert no_commit_created(hooked_repo)


def test_warning_allows_commit(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    result = commit_with_hooks("chore: x\n\nCo-authored-by: John")  # malformed -> warn
    assert result.returncode == 0, result.stderr
    assert "allowed with warnings" in result.stderr
    assert "malformed_trailer" in result.stderr


COMMENTED = "feat: x\n\n# Co-authored-by: Claude <noreply@anthropic.com>\n"


@pytest.mark.parametrize(
    ("cleanup", "expected"),
    [
        (None, "subject\n\n# kept by default with -m/-F\n"),
        ("strip", "subject\n"),
        ("whitespace", "subject\n\n# kept by default with -m/-F\n"),
        ("verbatim", "subject\n\n\n# kept by default with -m/-F\n\n"),
    ],
)
def test_message_cleanup_matches_git_modes(hooked_repo, cleanup, expected) -> None:  # type: ignore[no-untyped-def]
    from commitguard.git.repository import Repository

    if cleanup:
        hooked_repo.git("config", "commit.cleanup", cleanup)
    raw = "subject\n\n\n# kept by default with -m/-F\n\n"
    assert Repository.discover(hooked_repo.path).cleanup_message(raw) == expected


def test_scissors_section_is_dropped(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    from commitguard.git.repository import Repository

    raw = (
        "subject\n\n"
        "# ------------------------ >8 ------------------------\n"
        "+Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    assert Repository.discover(hooked_repo.path).cleanup_message(raw) == "subject\n"


def test_comment_lines_are_ignored_when_git_strips_them(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.git("config", "commit.cleanup", "strip")
    message = hooked_repo.path.parent / "MSG"
    message.write_text(COMMENTED)
    result = hooked_repo.run("commit", "--allow-empty", "-F", str(message))
    assert result.returncode == 0, result.stderr
    assert "Claude" not in hooked_repo.git("log", "-1", "--format=%B")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX editor script")
def test_verbose_commit_diff_below_scissors_is_not_evaluated(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    # `git commit -v` appends the diff below the scissors line; it is never stored.
    (hooked_repo.path / "README.md").write_text("Co-authored-by: Claude <noreply@anthropic.com>\n")
    hooked_repo.git("add", "README.md")
    editor = hooked_repo.path.parent / "editor.sh"
    editor.write_text(
        "#!/bin/sh\n"
        'printf "docs: document trailers\\n" | cat - "$1" > "$1.new" && mv "$1.new" "$1"\n'
    )
    editor.chmod(0o755)
    result = hooked_repo.run("commit", "-v", env={"GIT_EDITOR": str(editor)})
    assert result.returncode == 0, result.stderr
    assert hooked_repo.git("log", "-1", "--format=%s") == "docs: document trailers"


def test_malicious_message_is_plain_text(hooked_repo, commit_with_hooks, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "pwned"
    payloads = [
        f"$(touch {marker})",
        f"`touch {marker}`",
        f"; touch {marker}",
        f"&& touch {marker}",
        f"| touch {marker}",
        f"'; touch {marker}; echo '",
    ]
    for payload in payloads:
        result = commit_with_hooks(
            f"fix: {payload}\n\nCo-authored-by: Dev <dev@example.com> {payload}"
        )
        assert result.returncode in (0, 1), result.stderr  # never an internal error
    assert not marker.exists()


@pytest.mark.parametrize(
    ("message", "extra"),
    [
        ("x\n\nCo-authored-by:\nCo-authored-by: <invalid>\nCo-authored-by: Claude <>", ()),
        ("x\n\nCo-authored-by: " + "A" * 20_000 + " <a@example.com>", ()),
        ("x\n\n" + "Signed-off-by: A\n" * 1500, ()),  # beyond the trailer limit -> fail closed
        (
            "x",
            (
                "--author="
                + "".join(chr(0xFF00 + ord(c) - 0x20) for c in "Claude")
                + " <noreply@anthropic.com>",
            ),
        ),
        (
            "x",
            (
                "--author="
                + "".join(map(chr, (0x0645, 0x062D, 0x0645, 0x062F)))
                + " <m@example.com>",
            ),
        ),
        ("x", ("--author=" + chr(0x200B) + " <invalid-email>",)),
    ],
)
def test_malformed_metadata_never_crashes(
    commit_with_hooks, message: str, extra: tuple[str, ...]
) -> None:  # type: ignore[no-untyped-def]
    result = commit_with_hooks(message, *extra)
    assert result.returncode in (0, 1), result.stderr
    assert "could not verify repository policy" not in result.stderr


def test_invalid_configuration_fails_closed(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    (hooked_repo.path / ".commitguard.yaml").write_text("version: 1\npolicies:\n  nope: {}\n")
    result = commit_with_hooks("feat: perfectly clean")
    assert result.returncode != 0  # git reports any hook failure as 1
    assert "CommitGuard could not verify repository policy." in result.stderr
    assert "unknown policy id" in result.stderr
    assert "Commit blocked because the security check could not be completed." in result.stderr
    assert "commitguard doctor" in result.stderr
    assert no_commit_created(hooked_repo)


def test_disabled_commit_msg_enforcement_is_visible(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    (hooked_repo.path / ".commitguard.yaml").write_text(
        "version: 1\nenforcement:\n  commit_msg: false\n"
    )
    result = commit_with_hooks(AI_MSG)
    assert result.returncode == 0
    assert "commit-msg enforcement is disabled by configuration" in result.stderr


def test_no_verify_bypasses_local_commit_hooks(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    """Documented limitation: Git's intentional bypass works; CommitGuard does not fight it."""
    result = commit_with_hooks(AI_MSG, "--no-verify")
    assert result.returncode == 0
    assert "Claude" in hooked_repo.git("log", "-1", "--format=%B")


def test_repository_cannot_shadow_the_commitguard_package(hooked_repo, commit_with_hooks) -> None:  # type: ignore[no-untyped-def]
    """Hooks run from the work tree; `python -P` keeps a local commitguard/ from being imported."""
    fake = hooked_repo.path / "commitguard"
    fake.mkdir()
    (fake / "__init__.py").write_text("")
    (fake / "__main__.py").write_text(
        "import pathlib, sys\npathlib.Path('SHADOWED').write_text('x')\nsys.exit(0)\n"
    )
    result = commit_with_hooks("feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    assert result.returncode == 1
    assert "COMMIT BLOCKED" in result.stderr
    assert not (hooked_repo.path / "SHADOWED").exists()
