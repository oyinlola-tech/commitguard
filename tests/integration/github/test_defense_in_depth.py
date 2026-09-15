"""Phase 4 end-to-end scenario: local hooks, a bypass, CI enforcement and a merge gate.

Real: the repository, the bare "GitHub" remote, CommitGuard's Git hooks, the
--no-verify bypass reaching the remote, `commitguard ci github` runs on
pull request and push events, the fix, and the final merge.

Modelled: GitHub branch protection. It cannot run offline, so
`required_check_gate` reproduces its documented rule - a pull request can be
merged only if every required status check concluded successfully - using the
real exit code of the CommitGuard check. It does not prove GitHub is configured.
"""

import subprocess
import sys
from pathlib import Path

REQUIRED_CHECKS = ("commitguard",)


def required_check_gate(check_conclusions: dict[str, str]) -> bool:
    """Model of GitHub's "Require status checks to pass before merging"."""
    return all(check_conclusions.get(name) == "success" for name in REQUIRED_CHECKS)


def conclusion(returncode: int) -> str:
    return "success" if returncode == 0 else "failure"


def cg(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_defense_in_depth(hub, run_ci, gh, get_remote_refs) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    ai_message = "feat: add payments\n\nCo-authored-by: Claude <noreply@anthropic.com>"

    # Steps 1-2: repository with ai_coauthor = block on main (created by the fixture).
    assert "action: block" in dev.git("show", "main:.commitguard.yaml")
    base = dev.git("rev-parse", "main")
    assert cg(dev.path, "install").returncode == 0
    dev.git("checkout", "--quiet", "-b", "feature")

    # Step 3: clean commit passes the local hook ...
    local = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "feat: implement authentication"],
        cwd=dev.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert local.returncode == 0, local.stderr
    clean_sha = dev.git("rev-parse", "HEAD")
    assert (
        subprocess.run(
            ["git", "push", "--quiet", "origin", "feature"],
            cwd=dev.path,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
    # ... and the GitHub check.
    first = run_ci(
        hub.ci_clone(checkout=clean_sha, name="ci-1"), "pull_request", gh.pr_event(base, clean_sha)
    )
    assert first.returncode == 0, first.output

    # Step 4: AI-attributed commit is blocked locally.
    blocked = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", ai_message],
        cwd=dev.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked.returncode != 0
    assert "COMMIT BLOCKED" in blocked.stderr

    # Step 5: intentional bypass of local hooks.
    dev.git("commit", "--no-verify", "--allow-empty", "-m", ai_message)
    ai_sha = dev.git("rev-parse", "HEAD")
    push = subprocess.run(
        ["git", "push", "--no-verify", "origin", "feature"],
        cwd=dev.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert push.returncode == 0, push.stderr

    # Step 6: the violating commit reached the unprotected remote (the local-hook limitation).
    assert get_remote_refs(hub.bare)["refs/heads/feature"] == ai_sha

    # Steps 7-8: the pull request check fails.
    ci = hub.ci_clone(checkout=ai_sha, name="ci-2")
    hub.synthetic_merge_checkout(ci, base, ai_sha)
    failing = run_ci(ci, "pull_request", gh.pr_event(base, ai_sha))
    assert failing.returncode == 1, failing.output
    assert "Result: BLOCK" in failing.stdout
    assert ai_sha[:7] in failing.stdout

    # Steps 9-10: with the check required, the merge is refused (modelled gate).
    assert required_check_gate({"commitguard": conclusion(failing.returncode)}) is False

    # Step 11: the developer corrects the commit (local hooks run and pass).
    amend = subprocess.run(
        ["git", "commit", "--amend", "--allow-empty", "-m", "feat: add payments"],
        cwd=dev.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert amend.returncode == 0, amend.stderr
    fixed_sha = dev.git("rev-parse", "HEAD")
    push = subprocess.run(
        ["git", "push", "--force-with-lease", "origin", "feature"],
        cwd=dev.path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert push.returncode == 0, push.stderr

    # Step 12: the check passes.
    passing = run_ci(
        hub.ci_clone(checkout=fixed_sha, name="ci-3"), "pull_request", gh.pr_event(base, fixed_sha)
    )
    assert passing.returncode == 0, passing.output
    assert "commits scanned: 2" in passing.stdout

    # Step 13: merge allowed; the merge is performed on the remote and the
    # post-merge push check on main passes as well.
    assert required_check_gate({"commitguard": conclusion(passing.returncode)}) is True
    subprocess.run(
        ["git", "push", "--quiet", "--no-verify", "origin", f"{fixed_sha}:refs/heads/main"],
        cwd=dev.path,
        check=True,
        capture_output=True,
    )
    assert get_remote_refs(hub.bare)["refs/heads/main"] == fixed_sha
    post = run_ci(
        hub.ci_clone(checkout=fixed_sha, name="ci-4"), "push", gh.push_event(base, fixed_sha)
    )
    assert post.returncode == 0, post.output
    remote_main = subprocess.run(
        ["git", "rev-list", "refs/heads/main"],
        cwd=hub.bare,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert ai_sha not in remote_main
    assert fixed_sha in remote_main
