"""Phase 3 acceptance scenario with real processes, a real repository and a bare remote.

Every CommitGuard invocation below is a separate `python -m commitguard`
process, and every Git operation runs the installed hooks for real.
"""

import subprocess
import sys
from pathlib import Path

USER_HOOK = '#!/bin/sh\necho "unrelated pre-push hook" >&2\ncat > /dev/null\nexit 0\n'


def cg(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_phase3_acceptance(git_repo, bare_remote: Path, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    repo = git_repo
    hooks = repo.path / ".git" / "hooks"

    # 1-2. repository with a remote and an unrelated pre-existing hook
    repo.git("remote", "add", "origin", str(bare_remote))
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-push").write_text(USER_HOOK, newline="\n")
    (hooks / "pre-push").chmod(0o755)

    # 3. initialise CommitGuard
    assert cg(repo.path, "init").returncode == 0

    # 4. install hooks
    installed = cg(repo.path, "install")
    assert installed.returncode == 0, installed.stderr
    assert "preserved as pre-push.pre-commitguard" in installed.stdout

    # 5. AI co-author policy = block (explicitly)
    (repo.path / ".commitguard.yaml").write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    enabled: true\n    action: block\n"
        "enforcement:\n  pre_commit: true\n  commit_msg: true\n  pre_push: true\n"
    )

    # 6-7. clean commit succeeds
    clean = repo.run("commit", "--allow-empty", "-m", "feat: implement authentication")
    assert clean.returncode == 0, clean.stderr
    clean_sha = repo.head()

    # 8-9. AI-attributed commit is blocked locally
    ai_message = "feat: add payments\n\nCo-authored-by: Claude <noreply@anthropic.com>"
    blocked = repo.run("commit", "--allow-empty", "-m", ai_message)
    assert blocked.returncode != 0
    assert "COMMIT BLOCKED" in blocked.stderr
    assert repo.head() == clean_sha

    # 10. controlled path around local commit hooks
    assert repo.run("commit", "--no-verify", "--allow-empty", "-m", ai_message).returncode == 0
    violating_sha = repo.head()

    # 11-13. push is blocked and the remote receives nothing
    push = repo.run("push", "origin", "main")
    assert push.returncode != 0
    assert "PUSH BLOCKED" in push.stderr
    assert violating_sha[:7] in push.stderr
    assert "No changes were pushed to the remote repository." in push.stderr
    assert "unrelated pre-push hook" not in push.stderr  # CommitGuard ran first and stopped Git
    assert get_remote_refs(bare_remote) == {}

    # 14. the developer fixes the commit themselves (hooks run and pass)
    fixed = repo.run("commit", "--amend", "--allow-empty", "-m", "feat: add payments")
    assert fixed.returncode == 0, fixed.stderr

    # 15-16. push succeeds, both CommitGuard and the preserved hook ran
    push = repo.run("push", "origin", "main")
    assert push.returncode == 0, push.stderr
    assert "2 outgoing commits checked, no policy violations" in push.stderr
    assert "unrelated pre-push hook" in push.stderr
    assert get_remote_refs(bare_remote) == {"refs/heads/main": repo.head()}

    # 17-18. doctor reports a healthy installation
    doctor = cg(repo.path, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "Status: HEALTHY" in doctor.stdout
    for hook in ("pre-commit", "commit-msg", "pre-push"):
        assert f"{hook} installed" in doctor.stdout

    # 19-20. uninstall removes CommitGuard and restores the unrelated hook
    removed = cg(repo.path, "uninstall")
    assert removed.returncode == 0, removed.stderr
    assert (hooks / "pre-push").read_text() == USER_HOOK
    assert not (hooks / "pre-commit").exists()
    assert not (hooks / "commit-msg").exists()
    assert not (hooks / "pre-push.pre-commitguard").exists()

    # afterwards Git no longer enforces anything locally
    assert repo.run("commit", "--allow-empty", "-m", ai_message).returncode == 0
