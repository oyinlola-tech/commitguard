"""pre-push enforcement through real `git push` to a local bare remote."""

import sys

import pytest

from commitguard.git.repository import Repository
from commitguard.services.hooks import UpdateDisposition, plan_push

CLEAN = "feat: clean change\n"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
BOT = "build(deps): bump\n"


def push(repo, *args: str):  # type: ignore[no-untyped-def]
    return repo.run("push", "origin", *args)


def test_clean_push_succeeds(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    for index in range(3):
        hooked_repo.commit(f"feat: change {index}\n")
    result = push(hooked_repo, "main")
    assert result.returncode == 0, result.stderr
    assert "3 outgoing commits checked, no policy violations" in result.stderr
    assert get_remote_refs(bare_remote) == {"refs/heads/main": hooked_repo.head()}


def test_blocked_push_sends_nothing(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    bad = hooked_repo.commit(AI)
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    err = result.stderr
    assert "PUSH BLOCKED" in err
    assert "Commits checked: 2" in err
    assert "Violations: 1" in err
    assert bad[:7] in err
    assert "feat: add payment service" in err
    assert "Claude <noreply@anthropic.com>" in err
    assert "No changes were pushed to the remote repository." in err
    assert get_remote_refs(bare_remote) == {}


def test_violation_in_the_middle_blocks_all_commits(
    hooked_repo, bare_remote, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit("base\n")
    assert push(hooked_repo, "main").returncode == 0
    base = hooked_repo.head()
    hooked_repo.commit("A\n")
    hooked_repo.commit("B\n")
    hooked_repo.commit(AI)
    hooked_repo.commit("D\n")
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    assert "Commits checked: 4" in result.stderr
    assert "Violations: 1" in result.stderr
    assert "Allowed: 3" in result.stderr
    assert get_remote_refs(bare_remote) == {"refs/heads/main": base}


def test_summary_counts_multiple_violations_and_warnings(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    from commitguard.provenance.author import Identity

    bot = Identity(
        name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"
    )
    hooked_repo.commit(AI)
    hooked_repo.commit(CLEAN)
    hooked_repo.commit(AI)
    hooked_repo.commit(BOT, author=bot)
    hooked_repo.commit(AI)
    result = push(hooked_repo, "main")
    assert "Commits checked: 5" in result.stderr
    assert "Violations: 3" in result.stderr
    assert "Warnings: 1" in result.stderr
    assert "Allowed: 1" in result.stderr


def test_warning_only_push_is_allowed(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    from commitguard.provenance.author import Identity

    bot = Identity(
        name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"
    )
    hooked_repo.commit(BOT, author=bot)
    result = push(hooked_repo, "main")
    assert result.returncode == 0, result.stderr
    assert "PUSH ALLOWED WITH WARNINGS" in result.stderr
    assert "refs/heads/main" in get_remote_refs(bare_remote)


def test_multiple_refs_one_violation_blocks_entire_push(
    hooked_repo, bare_remote, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    hooked_repo.git("tag", "-a", "v1.0.0", "-m", "release")
    hooked_repo.git("checkout", "-q", "-b", "develop")
    hooked_repo.commit(AI)
    result = push(hooked_repo, "main", "develop", "v1.0.0")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr
    assert get_remote_refs(bare_remote) == {}  # atomic from the remote's point of view


def test_shared_commits_are_checked_once(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    hooked_repo.commit(CLEAN)
    hooked_repo.git("branch", "develop")
    hooked_repo.git("tag", "v1")
    result = push(hooked_repo, "main", "develop", "v1")
    assert result.returncode == 0, result.stderr
    assert "2 outgoing commits checked" in result.stderr


def test_new_branch_only_checks_commits_the_remote_lacks(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    for index in range(5):
        hooked_repo.commit(f"history {index}\n")
    assert push(hooked_repo, "main").returncode == 0
    hooked_repo.git("checkout", "-q", "-b", "feature")
    hooked_repo.commit("feature work\n")
    result = push(hooked_repo, "feature")
    assert result.returncode == 0, result.stderr
    assert "1 outgoing commit checked" in result.stderr


def test_branch_deletion_is_allowed_without_scanning(
    hooked_repo, bare_remote, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    hooked_repo.git("branch", "old")
    assert push(hooked_repo, "main", "old").returncode == 0
    result = push(hooked_repo, "--delete", "old")
    assert result.returncode == 0, result.stderr
    assert "No new commits to check" in result.stderr
    assert "refs/heads/old" not in get_remote_refs(bare_remote)


def test_up_to_date_push_is_quick_and_quiet(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    assert push(hooked_repo, "main").returncode == 0
    hooked_repo.git("tag", "later")  # a new ref pointing at an already-pushed commit
    result = push(hooked_repo, "later")
    assert result.returncode == 0
    assert "No new commits to check" in result.stderr


def test_annotated_tag_is_peeled_to_its_commit(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    assert push(hooked_repo, "main").returncode == 0
    hooked_repo.git("checkout", "-q", "--detach")
    hooked_repo.commit(AI)
    hooked_repo.git("tag", "-a", "v2", "-m", "release v2")
    result = push(hooked_repo, "v2")
    assert result.returncode != 0
    assert "ai_coauthor" in result.stderr
    assert "refs/tags/v2" not in get_remote_refs(bare_remote)


def test_tag_pointing_at_a_tree_is_handled(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    tree = hooked_repo.git("rev-parse", "HEAD^{tree}")
    hooked_repo.git("tag", "-a", "tree-tag", "-m", "a tree", tree)
    result = push(hooked_repo, "tree-tag")
    assert result.returncode == 0, result.stderr
    assert "refs/tags/tree-tag" in get_remote_refs(bare_remote)
    repo = Repository.discover(hooked_repo.path)
    tag_oid = hooked_repo.git("rev-parse", "tree-tag")
    from commitguard.git.push import parse_pre_push_input

    updates = parse_pre_push_input(f"refs/tags/tree-tag {tag_oid} refs/tags/tree-tag {'0' * 40}\n")
    plans, shas = plan_push(repo, updates, "origin", max_commits=100)
    assert plans[0].disposition is UpdateDisposition.NON_COMMIT
    assert shas == []


def test_force_push_checks_rewritten_commits(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    hooked_repo.commit("to be replaced\n")
    assert push(hooked_repo, "main").returncode == 0
    pushed = hooked_repo.head()
    hooked_repo.git("reset", "-q", "--hard", "HEAD~1")
    hooked_repo.commit(AI)
    result = push(hooked_repo, "--force", "main")
    assert result.returncode != 0
    assert "Commits checked: 1" in result.stderr
    assert get_remote_refs(bare_remote)["refs/heads/main"] == pushed


def test_merge_commits_are_analysed_without_rescanning_pushed_history(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit("base\n")
    assert push(hooked_repo, "main").returncode == 0
    hooked_repo.git("checkout", "-q", "-b", "topic")
    hooked_repo.commit("topic work\n")
    hooked_repo.git("checkout", "-q", "main")
    hooked_repo.commit("main work\n")
    hooked_repo.git("merge", "-q", "--no-ff", "--no-verify", "-m", "Merge topic", "topic")
    result = push(hooked_repo, "main")
    assert result.returncode == 0, result.stderr
    assert "3 outgoing commits checked" in result.stderr  # topic, main work, merge


def test_merge_bringing_in_ai_commit_is_blocked(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit("base\n")
    assert push(hooked_repo, "main").returncode == 0
    hooked_repo.git("checkout", "-q", "-b", "topic")
    hooked_repo.commit(AI)
    hooked_repo.git("checkout", "-q", "main")
    hooked_repo.git("merge", "-q", "--no-ff", "--no-verify", "-m", "Merge topic", "topic")
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    assert "Violations: 1" in result.stderr


def test_detached_head_push_uses_git_ref_information(
    hooked_repo, bare_remote, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    hooked_repo.git("checkout", "-q", "--detach")
    hooked_repo.commit("detached work\n")
    result = push(hooked_repo, "HEAD:refs/heads/from-detached")
    assert result.returncode == 0, result.stderr
    assert "refs/heads/from-detached" in get_remote_refs(bare_remote)


NTFS_FORBIDDEN = set('"<>|*?:')


@pytest.mark.parametrize(
    "branch",
    [
        'we"ird',
        "semi;colon",
        "$(touch${IFS}pwned)",
        "back`tick`",
        "pipe|and&amp",
        "unicod" + chr(0x00E9) + "-" + chr(0x6F22),
    ],
)
def test_malicious_branch_names(
    hooked_repo, bare_remote, get_remote_refs, tmp_path, branch: str
) -> None:  # type: ignore[no-untyped-def]
    if sys.platform.startswith("win") and NTFS_FORBIDDEN & set(branch):
        pytest.skip("loose ref files cannot contain this character on Windows")
    hooked_repo.commit(CLEAN)
    hooked_repo.git("checkout", "-q", "-b", branch)
    hooked_repo.commit(AI)
    result = push(hooked_repo, branch)
    assert result.returncode == 1, result.stderr
    assert "PUSH BLOCKED" in result.stderr
    assert get_remote_refs(bare_remote) == {}
    assert not (hooked_repo.path / "pwned").exists()
    assert not (tmp_path / "pwned").exists()


def test_push_to_url_without_named_remote(hooked_repo, bare_remote) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(AI)
    result = hooked_repo.run("push", str(bare_remote), "main")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr


def test_invalid_configuration_blocks_push(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    hooked_repo.commit(CLEAN)
    (hooked_repo.path / ".commitguard.yaml").write_text("version: 1\npolicies: [broken\n")
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    assert "Push blocked because the security check could not be completed." in result.stderr
    assert get_remote_refs(bare_remote) == {}


def test_disabled_pre_push_enforcement_is_visible(hooked_repo) -> None:  # type: ignore[no-untyped-def]
    (hooked_repo.path / ".commitguard.yaml").write_text(
        "version: 1\nenforcement:\n  pre_push: false\n"
    )
    hooked_repo.commit(AI)
    result = push(hooked_repo, "main")
    assert result.returncode == 0
    assert "pre-push enforcement is disabled by configuration" in result.stderr


def test_too_many_commits_fails_closed(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    (hooked_repo.path / ".commitguard.yaml").write_text(
        "version: 1\nenforcement:\n  max_push_commits: 2\n"
    )
    for index in range(3):
        hooked_repo.commit(f"c{index}\n")
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    assert "more than 2 commits" in result.stderr
    assert get_remote_refs(bare_remote) == {}


def test_no_verify_bypasses_local_push_hook(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    """Documented limitation: local hooks are user-controlled. Phase 4 adds server-side checks."""
    bad = hooked_repo.commit(AI)
    result = push(hooked_repo, "--no-verify", "main")
    assert result.returncode == 0
    assert get_remote_refs(bare_remote) == {"refs/heads/main": bad}


def test_malformed_stdin_fails_closed(git_repo) -> None:  # type: ignore[no-untyped-def]
    from typer.testing import CliRunner

    from commitguard.cli.app import app

    result = CliRunner().invoke(app, ["hook", "pre-push", "origin", "url"], input="garbage\n")
    assert result.exit_code == 2
    assert "could not verify repository policy" in result.output
