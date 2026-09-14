from pathlib import Path

import pytest

from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.git import GitError, NotAGitRepositoryError
from commitguard.git.commands import git_version, git_version_supported
from commitguard.git.repository import Repository
from commitguard.provenance.author import Identity

RLO = chr(0x202E)


def test_git_version_is_supported() -> None:
    assert git_version()[0] >= 2
    assert git_version_supported()


def test_discover_from_root_and_subdirectory(git_repo) -> None:  # type: ignore[no-untyped-def]
    sub = git_repo.path / "a" / "b"
    sub.mkdir(parents=True)
    for start in (git_repo.path, sub):
        repo = Repository.discover(start)
        assert repo.root == git_repo.path.resolve()
        assert repo.git_dir == (git_repo.path / ".git").resolve()


def test_discover_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(NotAGitRepositoryError):
        Repository.discover(tmp_path)


def test_hooks_dir_honours_core_hooks_path(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    assert repo.hooks_dir() == (git_repo.path / ".git" / "hooks").resolve()
    git_repo.git("config", "core.hooksPath", str(git_repo.path / "custom-hooks"))
    assert repo.hooks_dir() == git_repo.path / "custom-hooks"


def test_config_get(git_repo) -> None:  # type: ignore[no-untyped-def]
    repo = Repository.discover(git_repo.path)
    assert repo.config_get("commit.gpgsign") == "false"
    assert repo.config_get("commitguard.unset") is None
    with pytest.raises(UnsafeInputError):
        repo.config_get("--global")


def test_read_commit_metadata(git_repo) -> None:  # type: ignore[no-untyped-def]
    first = git_repo.commit("initial\n")
    second = git_repo.commit(
        "feat: implement authentication\n\nCo-authored-by: Claude <noreply@anthropic.com>\n",
        author=Identity(name="Ada Lovelace", email="ada@example.com"),
    )
    commit = Repository.discover(git_repo.path).read_commit("HEAD")

    assert commit.sha == second
    assert commit.parents == (first,)
    assert commit.author == Identity(name="Ada Lovelace", email="ada@example.com")
    assert commit.committer == Identity(name="Test Committer", email="committer@example.com")
    assert commit.message == (
        "feat: implement authentication\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
    )
    assert commit.authored_at is not None
    assert commit.authored_at.tzinfo is not None
    assert not commit.is_pending
    assert commit.subject == "feat: implement authentication"


def test_hostile_message_round_trips_exactly(git_repo) -> None:  # type: ignore[no-untyped-def]
    message = (
        "fix: $(rm -rf ~) `id`\n\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\x1b[1A\x1b[2K\r\n"
        f"{RLO}gnp.exe | %x00 %H\n"
    )
    git_repo.commit(message)
    assert Repository.discover(git_repo.path).read_commit().message == message


def test_mailmap_cannot_disguise_an_identity(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("x\n", author=Identity(name="Claude", email="noreply@anthropic.com"))
    (git_repo.path / ".mailmap").write_text(
        "Ada Lovelace <ada@example.com> <noreply@anthropic.com>\n"
    )
    git_repo.git("config", "log.mailmap", "true")
    commit = Repository.discover(git_repo.path).read_commit()
    assert commit.author == Identity(name="Claude", email="noreply@anthropic.com")


def test_replace_refs_cannot_substitute_the_commit(git_repo) -> None:  # type: ignore[no-untyped-def]
    real = git_repo.commit("Co-authored-by: Claude <noreply@anthropic.com>\n")
    git_repo.git("checkout", "--quiet", "--orphan", "decoy")
    decoy = git_repo.commit("innocent\n")
    git_repo.git("replace", real, decoy)
    commit = Repository.discover(git_repo.path).read_commit(real)
    assert commit.sha == real
    assert "Claude" in commit.message


@pytest.mark.parametrize("revision", ["--output=/tmp/commitguard-pwned", "-p", "HEAD\nx"])
def test_option_injection_is_refused(git_repo, revision: str) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("x\n")
    with pytest.raises(UnsafeInputError):
        Repository.discover(git_repo.path).read_commit(revision)
    assert not Path("/tmp/commitguard-pwned").exists()  # noqa: S108


def test_unknown_revision(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("x\n")
    with pytest.raises(GitError, match="does not resolve"):
        Repository.discover(git_repo.path).read_commit("does-not-exist")


def test_reading_does_not_modify_the_repository(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("x\n")
    before = git_repo.git("for-each-ref") + git_repo.git("status", "--porcelain")
    repo = Repository.discover(git_repo.path)
    repo.read_commit()
    repo.hooks_dir()
    after = git_repo.git("for-each-ref") + git_repo.git("status", "--porcelain")
    assert before == after


def test_list_commits_single_revision_and_range(git_repo) -> None:  # type: ignore[no-untyped-def]
    first = git_repo.commit("one\n")
    second = git_repo.commit("two\n")
    third = git_repo.commit("three\n")
    repo = Repository.discover(git_repo.path)
    assert repo.list_commits("HEAD", max_count=10) == [third]
    assert repo.list_commits(f"{first}..HEAD", max_count=10) == [second, third]
    assert repo.list_commits("HEAD..HEAD", max_count=10) == []


def test_list_commits_refuses_to_silently_truncate(git_repo) -> None:  # type: ignore[no-untyped-def]
    base = git_repo.commit("base\n")
    for index in range(3):
        git_repo.commit(f"c{index}\n")
    with pytest.raises(GitError, match="more than 2"):
        Repository.discover(git_repo.path).list_commits(f"{base}..HEAD", max_count=2)


def test_read_commits_batch_preserves_exact_messages(git_repo) -> None:  # type: ignore[no-untyped-def]
    messages = [
        "plain\n",
        "no trailing newline",
        "trailing blank lines\n\n\n",
        "looks like a record separator: %x00 00deadbeef00\n",
        "Co-authored-by: Claude <noreply@anthropic.com>\n",
    ]
    shas = [git_repo.commit(message) for message in messages]
    commits = Repository.discover(git_repo.path).read_commits(shas)
    assert [c.sha for c in commits] == shas
    for commit in commits:
        assert commit.sha is not None
        single = Repository.discover(git_repo.path).read_commits([commit.sha])[0]
        assert single.message == commit.message  # batching does not alter messages
    assert commits[0].message == "plain\n"
    assert commits[4].trailers[0].name == "Claude"


def test_read_commits_rejects_non_sha_input(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("x\n")
    with pytest.raises(UnsafeInputError):
        Repository.discover(git_repo.path).read_commits(["HEAD"])


def test_pending_identities(git_repo) -> None:  # type: ignore[no-untyped-def]
    author, committer = Repository.discover(git_repo.path).pending_identities()
    assert author == Identity(name="Test Author", email="author@example.com")
    assert committer == Identity(name="Test Committer", email="committer@example.com")


def test_commit_read_from_git_has_trailers(git_repo) -> None:  # type: ignore[no-untyped-def]
    git_repo.commit("feat\n\nCo-authored-by: John Doe <john@example.com>\n")
    (trailer,) = Repository.discover(git_repo.path).read_commit().trailers
    assert trailer.email == "john@example.com"
