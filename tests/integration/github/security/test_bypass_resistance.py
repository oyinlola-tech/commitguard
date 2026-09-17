"""Bypass-resistance experiments: local enforcement versus server-side enforcement.

Each experiment performs a real bypass against the local layer (real Git, real
CommitGuard hooks) and then evaluates the pushed commits the way the
authoritative layer does (``commitguard ci github`` on the pull request, with
policy read from the trusted base commit). The record states what was observed
at each layer; nothing is assumed.

GitHub branch protection cannot run offline. Where a merge decision is stated,
it uses ``required_check_gate`` - the documented rule "a required status check
must conclude successfully" - applied to the real check exit code, and says so.
"""

import subprocess
import sys
from pathlib import Path

AI = "feat: add payments\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
REQUIRED_CHECKS = ("commitguard",)


def required_check_gate(conclusions: dict[str, str]) -> bool:
    """Model of GitHub's "Require status checks to pass before merging" (not GitHub itself)."""
    return all(conclusions.get(name) == "success" for name in REQUIRED_CHECKS)


def cg(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def git_run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def server_check(hub, run_ci, gh, base: str, head: str, name: str):  # type: ignore[no-untyped-def]
    ci = hub.ci_clone(checkout=head, name=name)
    hub.synthetic_merge_checkout(ci, base, head)
    return run_ci(ci, "pull_request", gh.pr_event(base, head))


def _start(hub):  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    assert cg(dev.path, "install").returncode == 0
    dev.git("checkout", "--quiet", "-b", "feature")
    return dev, base


def test_no_verify_commit_and_push_are_caught_server_side(hub, run_ci, gh, observe) -> None:  # type: ignore[no-untyped-def]
    dev, base = _start(hub)
    local = git_run(dev.path, "commit", "--allow-empty", "-m", AI)
    assert local.returncode != 0  # the hook blocks a normal commit
    bypass = git_run(dev.path, "commit", "--no-verify", "--allow-empty", "-m", AI)
    assert bypass.returncode == 0
    head = dev.git("rev-parse", "HEAD")
    pushed = git_run(dev.path, "push", "--no-verify", "origin", "feature")
    assert pushed.returncode == 0

    check = server_check(hub, run_ci, gh, base, head, "ci-no-verify")
    assert check.returncode == 1, check.output
    merge_allowed = required_check_gate(
        {"commitguard": "success" if check.returncode == 0 else "failure"}
    )
    assert merge_allowed is False
    observe(
        experiment="no-verify",
        area="local hooks",
        attack="git commit --no-verify and git push --no-verify with an AI co-author trailer",
        expected="Local hooks are skipped; the pull request check blocks the commit",
        observed=(
            f"normal commit exit {local.returncode}; --no-verify commit exit {bypass.returncode}; "
            f"--no-verify push exit {pushed.returncode}; pull request check exit {check.returncode} "
            f"(BLOCK); modelled required-check gate allows merge: {merge_allowed}"
        ),
        consequence="The violating commit reaches the remote branch but cannot be merged while the check is required",
        mitigation="Server-side check (GitHub Actions or GitHub App) required by branch protection or a ruleset",
        limitation="Without a required check, a --no-verify push can be merged; CommitGuard cannot configure branch protection",
        outcome="detected",
    )


def test_deleted_hooks_are_reported_by_doctor_and_caught_server_side(
    hub, run_ci, gh, observe
) -> None:  # type: ignore[no-untyped-def]
    dev, base = _start(hub)
    hooks = Path(dev.git("rev-parse", "--git-path", "hooks"))
    hooks = hooks if hooks.is_absolute() else dev.path / hooks
    for name in ("pre-commit", "commit-msg", "pre-push"):
        (hooks / name).unlink()
    local = git_run(dev.path, "commit", "--allow-empty", "-m", AI)
    assert local.returncode == 0  # nothing left to enforce locally
    head = dev.git("rev-parse", "HEAD")
    assert git_run(dev.path, "push", "origin", "feature").returncode == 0
    doctor = cg(dev.path, "doctor")
    missing_reported = (
        "missing" in doctor.stdout.lower() or "not installed" in doctor.stdout.lower()
    )
    check = server_check(hub, run_ci, gh, base, head, "ci-deleted")
    assert check.returncode == 1, check.output
    observe(
        experiment="deleted-hooks",
        area="local hooks",
        attack="Delete the pre-commit, commit-msg and pre-push hook files, then commit and push",
        expected="Local enforcement is gone; doctor reports it; the pull request check blocks",
        observed=(
            f"commit exit {local.returncode} (allowed); doctor exit {doctor.returncode}, reports "
            f"missing hooks: {missing_reported}; pull request check exit {check.returncode} (BLOCK)"
        ),
        consequence="Local prevention is lost silently until doctor is run",
        mitigation="commitguard doctor; server-side required check",
        limitation="Git gives hooks no integrity protection; a developer controls their own .git/hooks",
        outcome="detected",
    )


def test_modified_hook_is_detected_by_checksum_and_caught_server_side(
    hub, run_ci, gh, observe
) -> None:  # type: ignore[no-untyped-def]
    dev, base = _start(hub)
    hooks = Path(dev.git("rev-parse", "--git-path", "hooks"))
    hooks = hooks if hooks.is_absolute() else dev.path / hooks
    for name in ("pre-commit", "commit-msg"):
        path = hooks / name
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('commitguard_run "$@" || exit $?', "exit 0"), encoding="utf-8")
    local = git_run(dev.path, "commit", "--allow-empty", "-m", AI)
    head = dev.git("rev-parse", "HEAD")
    assert git_run(dev.path, "push", "--no-verify", "origin", "feature").returncode == 0
    doctor = cg(dev.path, "doctor")
    modified_reported = "modified" in doctor.stdout.lower()
    check = server_check(hub, run_ci, gh, base, head, "ci-modified")
    assert local.returncode == 0
    assert check.returncode == 1, check.output
    observe(
        experiment="modified-hooks",
        area="local hooks",
        attack="Edit the managed hook block so it exits 0 before running CommitGuard",
        expected="The edited hook allows the commit; doctor flags the checksum mismatch; the pull request check blocks",
        observed=(
            f"commit exit {local.returncode}; doctor reports modified hooks: {modified_reported}; "
            f"pull request check exit {check.returncode} (BLOCK)"
        ),
        consequence="Local prevention silently disabled for this clone",
        mitigation="Managed-block checksum reported by commitguard doctor; server-side required check",
        limitation="A checksum detects edits only when doctor runs; it does not prevent them",
        outcome="detected",
    )
    assert modified_reported, doctor.stdout


def test_redirected_hooks_path_and_fresh_clone_are_caught_server_side(
    hub, run_ci, gh, observe
) -> None:  # type: ignore[no-untyped-def]
    dev, base = _start(hub)
    empty = hub.tmp / "empty-hooks"
    empty.mkdir()
    dev.git("config", "core.hooksPath", str(empty))
    redirected = git_run(dev.path, "commit", "--allow-empty", "-m", AI)
    head = dev.git("rev-parse", "HEAD")
    assert git_run(dev.path, "push", "origin", "feature").returncode == 0

    copy = hub.ci_clone(name="fresh-clone")  # hooks are not cloned
    copy.git("checkout", "--quiet", "-B", "copy", "origin/feature")
    copied = git_run(copy.path, "commit", "--allow-empty", "-m", AI)
    copy_head = copy.git("rev-parse", "HEAD")
    assert git_run(copy.path, "push", "--quiet", "origin", "copy").returncode == 0

    first = server_check(hub, run_ci, gh, base, head, "ci-hookspath")
    second = server_check(hub, run_ci, gh, base, copy_head, "ci-clone")
    assert redirected.returncode == 0
    assert copied.returncode == 0
    assert first.returncode == 1, first.output
    assert second.returncode == 1, second.output
    observe(
        experiment="hooks-path-and-clone",
        area="local hooks",
        attack="Point core.hooksPath at an empty directory; separately, commit from a fresh clone without installing hooks",
        expected="Both commits are allowed locally; both pull request checks block",
        observed=(
            f"core.hooksPath commit exit {redirected.returncode}; fresh clone commit exit "
            f"{copied.returncode}; pull request checks exit {first.returncode} and {second.returncode}"
        ),
        consequence="Any clone without CommitGuard hooks has no local enforcement",
        mitigation="Server-side required check; commitguard install --global for developer machines",
        limitation="Hooks are per clone and opt-in by design of Git",
        outcome="detected",
    )


def test_pull_request_cannot_weaken_its_own_policy(hub, run_ci, gh, observe) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "feature", base)
    variants = {
        "disable": "version: 1\npolicies:\n  ai_coauthor:\n    enabled: false\n",
        "allow": "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n",
        "delete": None,
    }
    exits = {}
    for index, (name, content) in enumerate(variants.items()):
        dev.git("checkout", "--quiet", "-B", f"tamper-{name}", base)
        if content is None:
            dev.git("rm", "--quiet", ".commitguard.yaml")
            dev.git("commit", "--quiet", "--no-verify", "-m", "chore: remove policy")
        else:
            dev.commit(f"chore: relax policy ({name})\n", files={".commitguard.yaml": content})
        head = dev.commit(AI)
        dev.push(f"tamper-{name}")
        result = server_check(hub, run_ci, gh, base, head, f"ci-tamper-{index}")
        exits[name] = result.returncode
        assert result.returncode == 1, (name, result.output)
        assert f"Policy: pull request base ({base[:12]})" in result.stdout
    observe(
        experiment="policy-tampering-in-pull-request",
        area="repository configuration",
        attack="In the pull request itself, disable ai_coauthor, set it to allow, or delete .commitguard.yaml",
        expected="Policy is read from the pull request base; every variant is blocked",
        observed="pull request check exit codes: "
        + ", ".join(f"{k}={v}" for k, v in exits.items()),
        consequence="A pull request cannot relax the policy that evaluates it",
        mitigation="Trusted policy source: the base commit (Phase 4)",
        limitation="Once a relaxing change is merged into the base branch it applies to later pull requests, unless an organization mandatory policy (GitHub App) sets a floor",
        outcome="prevented",
    )


def test_ci_bypass_by_removing_the_workflow_leaves_no_successful_check(
    hub, run_ci, gh, observe
) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "no-workflow", base)
    head = dev.commit(
        AI, files={".github/workflows/commitguard.yml": "# removed by the pull request\n"}
    )
    dev.push("no-workflow")
    # The workflow did not run, so there is no CommitGuard check conclusion at all.
    merge_allowed = required_check_gate({})
    assert merge_allowed is False
    observe(
        experiment="ci-workflow-removal",
        area="GitHub Actions",
        attack="A pull request removes or neuters the CommitGuard workflow so no check runs",
        expected="No successful required check exists, so a required-check gate refuses the merge",
        observed=f"no check conclusion for {head[:12]}; modelled gate allows merge: {merge_allowed}",
        consequence="Only branch protection that requires the check stops the merge; the GitHub App check does not depend on the repository's workflow files",
        mitigation="Require the check in branch protection or a ruleset; prefer the GitHub App, which runs outside the repository",
        limitation="Modelled gate, not GitHub: verifying the real branch protection needs a GitHub repository (see docs/research/github-enforcement-validation.md)",
        outcome="prevented",
    )
