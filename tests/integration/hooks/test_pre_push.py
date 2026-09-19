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


# --------------------------------------------------------------------------- #
# remediation.fix_on_push: rewrite unpushed commits the commit-msg hook never saw
# --------------------------------------------------------------------------- #
FIX_ON_PUSH = "version: 1\nremediation:\n  fix_on_push: true\n"


@pytest.fixture
def fixing_repo(hooked_repo):  # type: ignore[no-untyped-def]
    (hooked_repo.path / ".commitguard.yaml").write_text(FIX_ON_PUSH, encoding="utf-8")
    return hooked_repo


def messages(repo, ref: str = "main") -> str:  # type: ignore[no-untyped-def]
    return repo.git("log", ref, "--format=%B")


def test_fix_on_push_cleans_unpushed_commits_then_the_next_push_succeeds(
    fixing_repo, bare_remote, get_remote_refs
) -> None:  # type: ignore[no-untyped-def]
    """`repo.commit` uses --no-verify: exactly the commits commit-msg never sees."""
    first = fixing_repo.commit("feat: one\n")
    fixing_repo.commit(AI)
    fixing_repo.commit("feat: three\n")

    stopped = push(fixing_repo, "main")
    assert stopped.returncode != 0, "the push carrying the old commits must not proceed"
    assert "CLEANED 2 unpushed commits" in stopped.stderr
    assert "removed: Co-authored-by: Claude <noreply@anthropic.com>" in stopped.stderr
    assert "Run `git push` again" in stopped.stderr
    assert get_remote_refs(bare_remote) == {}, "nothing may reach the remote on this push"

    assert "Claude" not in messages(fixing_repo)
    assert fixing_repo.git("rev-parse", "HEAD~2") == first, "commits before it keep their id"

    again = push(fixing_repo, "main")
    assert again.returncode == 0, again.stderr
    assert "no policy violations" in again.stderr
    assert "Claude" not in fixing_repo.git("--git-dir", str(bare_remote), "log", "--format=%B")


def test_fix_on_push_changes_only_the_message(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    (fixing_repo.path / "a.txt").write_text("content\n", encoding="utf-8")
    fixing_repo.git("add", "a.txt")
    fixing_repo.git(
        "commit",
        "--quiet",
        "--no-verify",
        "-m",
        "feat: add a\n\nBody stays.\n\nCo-authored-by: Claude <noreply@anthropic.com>",
        env={
            "GIT_AUTHOR_DATE": "2026-01-02T03:04:05+01:00",
            "GIT_COMMITTER_DATE": "2026-01-03T04:05:06+02:00",
        },
    )
    fields = "%T|%an|%ae|%aI|%cn|%ce|%cI"
    before = fixing_repo.git("log", "-1", f"--format={fields}")

    assert push(fixing_repo, "main").returncode != 0

    assert fixing_repo.git("log", "-1", f"--format={fields}") == before
    assert fixing_repo.git("log", "-1", "--format=%B") == "feat: add a\n\nBody stays."
    assert fixing_repo.git("status", "--porcelain") == "?? .commitguard.yaml"


def test_fix_on_push_keeps_human_trailers(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    fixing_repo.commit(
        "feat: pair\n\n"
        "Co-authored-by: Ada Lovelace <ada@example.com>\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\n"
    )
    assert push(fixing_repo, "main").returncode != 0
    stored = fixing_repo.git("log", "-1", "--format=%B")
    assert "Co-authored-by: Ada Lovelace <ada@example.com>" in stored
    assert "Claude" not in stored


def test_fix_on_push_never_rewrites_a_commit_another_remote_has(fixing_repo, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Once any remote has a commit it has been shared; rewriting it would fork history."""
    other = tmp_path / "other.git"
    fixing_repo.git("init", "--quiet", "--bare", str(other))
    fixing_repo.git("remote", "add", "other", str(other))
    bad = fixing_repo.commit(AI)
    fixing_repo.git("push", "--quiet", "--no-verify", "other", "main")
    fixing_repo.git("fetch", "--quiet", "other")

    result = push(fixing_repo, "main")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr
    assert "CLEANED" not in result.stderr
    assert fixing_repo.head() == bad


def test_fix_on_push_leaves_an_ai_identity_blocked(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    from commitguard.provenance.author import Identity

    bad = fixing_repo.commit(
        "feat: x\n", author=Identity(name="Claude", email="noreply@anthropic.com")
    )
    result = push(fixing_repo, "main")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr
    assert "CLEANED" not in result.stderr
    assert fixing_repo.head() == bad


def test_fix_on_push_never_touches_a_tag(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    bad = fixing_repo.commit(AI)
    fixing_repo.git("tag", "v1")
    result = push(fixing_repo, "v1")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr, "a normal block, not an internal error"
    assert "CLEANED" not in result.stderr
    assert fixing_repo.git("rev-parse", "v1^{commit}") == bad
    assert fixing_repo.head() == bad


def test_fix_on_push_does_nothing_during_a_merge(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    bad = fixing_repo.commit(AI)
    merge_head = fixing_repo.path / fixing_repo.git("rev-parse", "--git-path", "MERGE_HEAD")
    merge_head.write_text(bad + "\n", encoding="utf-8")
    result = push(fixing_repo, "main")
    assert result.returncode != 0
    assert "CLEANED" not in result.stderr
    assert fixing_repo.head() == bad


def test_fix_on_push_leaves_a_signed_commit_alone(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    """Rewriting would silently strip the signature, so it is never done."""
    plain = fixing_repo.commit(AI)
    raw = fixing_repo.git("cat-file", "commit", plain)
    headers, _, body = raw.partition("\n\n")
    signed = (
        headers
        + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n -----END PGP SIGNATURE-----"
        + "\n\n"
        + body
        + "\n"
    )
    result = fixing_repo.run("hash-object", "-t", "commit", "-w", "--stdin", input_text=signed)
    assert result.returncode == 0, result.stderr
    signed_sha = result.stdout.strip()
    fixing_repo.git("update-ref", "refs/heads/main", signed_sha)

    pushed = push(fixing_repo, "main")
    assert pushed.returncode != 0
    assert "CLEANED" not in pushed.stderr
    assert fixing_repo.head() == signed_sha


def test_fix_on_push_is_off_by_default(hooked_repo, bare_remote, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    bad = hooked_repo.commit(AI)
    result = push(hooked_repo, "main")
    assert result.returncode != 0
    assert "PUSH BLOCKED" in result.stderr
    assert "CLEANED" not in result.stderr
    assert hooked_repo.head() == bad
    assert get_remote_refs(bare_remote) == {}


def test_fix_on_push_is_all_or_nothing(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    """One unfixable commit means nothing is rewritten, even the fixable ones.

    Rewriting only the fixable commit would change history and still leave a
    push that is blocked, which is the worst of both.
    """
    from commitguard.provenance.author import Identity

    fixing_repo.commit("feat: x\n", author=Identity(name="Claude", email="noreply@anthropic.com"))
    bad = fixing_repo.commit(AI)
    result = push(fixing_repo, "main")
    assert result.returncode != 0
    assert "CLEANED" not in result.stderr
    assert fixing_repo.head() == bad


def test_fix_on_push_never_moves_a_branch_on_assumption(fixing_repo, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If removing the lines did not actually clear the block, nothing moves.

    Simulated by a stripping step that changes nothing: the rewritten commits
    are re-analysed before any branch is updated.
    """
    from commitguard.services import push_fix
    from commitguard.services.hooks import run_pre_push

    bad = fixing_repo.commit(AI)
    monkeypatch.setattr(push_fix, "strip_lines", lambda message, _lines: message)
    run = run_pre_push(
        Repository.discover(fixing_repo.path),
        "origin",
        f"refs/heads/main {bad} refs/heads/main {'0' * 40}\n",
    )
    assert run.fixed is None
    assert run.report is not None
    assert run.report.action.value == "block"
    assert fixing_repo.head() == bad


def test_fix_on_push_never_leaves_a_commit_with_no_message(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    bad = fixing_repo.commit("Co-authored-by: Claude <noreply@anthropic.com>\n")
    result = push(fixing_repo, "main")
    assert result.returncode != 0
    assert "CLEANED" not in result.stderr
    assert fixing_repo.head() == bad


def test_fix_on_push_refuses_when_the_analysed_text_is_not_the_stored_text(
    fixing_repo,
) -> None:  # type: ignore[no-untyped-def]
    """Line numbers from the analysis must refer to the message being rewritten.

    Git's own output always agrees; this guards the day it does not, by feeding
    an analysis of a different message than the one stored.
    """
    from commitguard.policies.defaults import default_policy_set
    from commitguard.services.analysis import Analyzer
    from commitguard.services.push_fix import BranchUpdate, fix_outgoing_commits

    bad = fixing_repo.commit("feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    repository = Repository.discover(fixing_repo.path)
    [stored] = repository.read_commits([bad])
    # The same attribution, but on a different line than the stored message has it.
    shifted = stored.model_copy(
        update={"message": "feat: x\n\nextra\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"}
    )
    analyzer = Analyzer.create(default_policy_set())
    fixed = fix_outgoing_commits(
        repository,
        analyzer,
        [BranchUpdate(local_ref="refs/heads/main", local_oid=bad, commits=(bad,))],
        {bad: shifted},
        {bad: analyzer.analyze(shifted)},
    )
    assert fixed is None
    assert fixing_repo.head() == bad


def test_doctor_reports_that_fix_on_push_is_on(fixing_repo) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    result = subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", "doctor"],
        cwd=fixing_repo.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "remediation.fix_on_push is on" in result.stdout
