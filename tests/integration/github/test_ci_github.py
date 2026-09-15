"""`commitguard ci github` against real Git history, offline.

Every run is a separate `python -P -m commitguard ci github` process reading a
GitHub event payload, exactly as the Action runs it.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

CLEAN = "feat: clean change {}\n"
WARN_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n"
BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
ESC = chr(0x1B)


def feature(hub, messages, *, branch: str = "feature"):  # type: ignore[no-untyped-def]
    """Create a branch from main with the given commit messages and push it."""
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", branch, base)
    shas = [dev.commit(message) for message in messages]
    dev.push("--force", branch)
    dev.git("checkout", "--quiet", "main")
    return base, shas


# --------------------------------------------------------------------------- #
# Pull requests
# --------------------------------------------------------------------------- #
def test_clean_pull_request_passes(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format(i) for i in range(4)])
    ci = hub.ci_clone()
    hub.synthetic_merge_checkout(ci, base, shas[-1])
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 0, result.output
    assert "commits scanned: 4" in result.stdout
    assert "Findings: 0" in result.stdout
    assert "Result: PASS" in result.stdout
    assert result.outputs == {
        "result": "allow",
        "conclusion": "success",
        "commits": "4",
        "violations": "0",
        "warnings": "0",
    }


def test_pull_request_with_ai_coauthor_fails(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format("A"), CLEAN.format("B"), gh.AI, CLEAN.format("D")])
    ci = hub.ci_clone()
    merge = hub.synthetic_merge_checkout(ci, base, shas[-1])
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 1, result.output
    out = result.stdout
    assert "commits scanned: 4" in out
    assert "Violations: 1" in out
    assert f"Commit:   {shas[2][:7]}" in out
    assert "AI coauthor detected" in out
    assert "Claude <noreply@anthropic.com>" in out
    assert "Rule:     ai_coauthor" in out
    assert "Action:   block" in out
    assert "Result: BLOCK" in out
    assert "does not automatically rewrite Git history" in out
    assert merge[:7] not in out  # GitHub's synthetic merge commit is never analysed
    assert "::error title=CommitGuard%3A AI coauthor detected::" in out
    assert result.outputs["result"] == "block"
    assert "Remediation" in result.summary


def test_multiple_violations_are_all_reported(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "feature", base)
    for message, author in [
        (gh.AI, None),
        (CLEAN.format(1), None),
        (gh.AI, None),
        ("deps\n", BOT),
        (CLEAN.format(2), None),
        (gh.AI, None),
        ("x\n\nCo-authored-by: John\n", None),
        (CLEAN.format(3), None),
    ]:
        dev.commit(message, author=author)
    head = dev.git("rev-parse", "HEAD")
    dev.push("feature")
    ci = hub.ci_clone(checkout=head)
    result = run_ci(ci, "pull_request", gh.pr_event(base, head))
    assert result.returncode == 1
    assert "commits scanned: 8" in result.stdout
    assert "Violations: 3" in result.stdout
    assert "Warnings: 2" in result.stdout
    assert "Allowed: 3" in result.stdout
    data = run_ci(ci, "pull_request", gh.pr_event(base, head), "--format", "json").json()
    rules = sorted(f["finding"]["rule_id"] for c in data["commits"] for f in c["findings"])
    assert rules == [
        "ai_coauthor",
        "ai_coauthor",
        "ai_coauthor",
        "bot_identity",
        "malformed_trailer",
    ]
    assert data["ci"]["pull_request_number"] == 7


def test_warning_only_passes_and_fail_on_warn_fails(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    hub.dev.commit("chore: warn on AI\n", files={".commitguard.yaml": WARN_CONFIG})
    hub.dev.push("main")
    base, shas = feature(hub, [gh.AI])
    ci = hub.ci_clone(checkout=shas[-1])
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 0, result.output
    assert "Result: PASS (with warnings)" in result.stdout
    assert "::warning title=CommitGuard%3A AI coauthor detected::" in result.stdout
    strict = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]), "--fail-on", "warn")
    assert strict.returncode == 1
    assert "FAILED (fail-on: warn)" in strict.stdout


def test_empty_range_passes_without_scanning_history(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    for i in range(5):
        hub.dev.commit(f"history {i}\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    hub.dev.push("main")
    head = hub.dev.git("rev-parse", "main")
    ci = hub.ci_clone(checkout=head)
    result = run_ci(ci, "pull_request", gh.pr_event(head, head))
    assert result.returncode == 0, result.output
    assert "commits scanned: 0" in result.stdout


def test_merge_commits_in_pull_request(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    dev.git("checkout", "--quiet", "-B", "feature", "main")
    dev.commit(CLEAN.format("feature"))
    dev.git("checkout", "--quiet", "main")
    dev.commit("main moves on\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    dev.push("main")
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "feature")
    dev.git("merge", "--quiet", "--no-ff", "--no-verify", "-m", "Merge main into feature", "main")
    head = dev.git("rev-parse", "HEAD")
    dev.push("feature")
    ci = hub.ci_clone(checkout=head)
    result = run_ci(ci, "pull_request", gh.pr_event(base, head), "--format", "json")
    assert result.returncode == 0, result.output  # main's AI commit is not introduced by the PR
    subjects = [c["subject"] for c in result.json()["commits"]]
    assert subjects == ["feat: clean change feature", "Merge main into feature"]


# --------------------------------------------------------------------------- #
# Policy and rule trust
# --------------------------------------------------------------------------- #
def test_pull_request_cannot_disable_its_own_policy(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "feature", base)
    tampered = gh.BLOCK_CONFIG.replace("action: block", "action: allow")
    dev.commit("chore: relax policy\n", files={".commitguard.yaml": tampered})
    head = dev.commit(gh.AI)
    dev.push("feature")
    ci = hub.ci_clone(checkout=head)
    assert (ci.path / ".commitguard.yaml").read_text() == tampered  # the checkout is tampered
    result = run_ci(ci, "pull_request", gh.pr_event(base, head))
    assert result.returncode == 1, result.output
    assert "Result: BLOCK" in result.stdout
    assert f"Policy: pull request base ({base[:12]})" in result.stdout
    assert ".commitguard.yaml is changed by the evaluated commits" in result.stdout
    assert "::notice title=CommitGuard::" in result.stdout


def test_config_input_is_read_from_trusted_commit(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    relaxed = "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "feature", base)
    dev.commit("x\n", files={".github/relaxed.yaml": relaxed})
    head = dev.commit(gh.AI)
    dev.push("feature")
    ci = hub.ci_clone(checkout=head)
    args = ("--config", ".github/relaxed.yaml")
    result = run_ci(ci, "pull_request", gh.pr_event(base, head), *args)
    assert result.returncode == 2  # the file does not exist at the trusted base
    assert "does not exist at trusted revision" in result.stdout


def test_rule_tampering_in_repository_has_no_effect(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    weakened = (
        "schema_version: 1\nagents:\n"
        "  - id: nobody\n    display_name: Nobody\n    names: [Nobody]\n"
    )
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "feature", base)
    dev.commit("chore: tweak rules\n", files={"rules/ai-identities.yaml": weakened})
    head = dev.commit(gh.AI)
    dev.push("feature")
    ci = hub.ci_clone(checkout=head)
    result = run_ci(ci, "pull_request", gh.pr_event(base, head))
    assert result.returncode == 1, result.output
    assert "ai_coauthor" in result.stdout


def test_invalid_trusted_policy_fails_closed(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    bad = "version: 1\npolicies:\n  ai_coauthors: {}\n"
    hub.dev.commit("chore: typo\n", files={".commitguard.yaml": bad})
    hub.dev.push("main")
    base, shas = feature(hub, [CLEAN.format(1)])
    ci = hub.ci_clone(checkout=shas[-1])
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 2
    assert "CommitGuard could not verify repository policy." in result.stdout
    assert "unknown policy id" in result.stdout
    assert "Security validation could not be completed." in result.stdout
    assert "Result: FAILED" in result.stdout
    assert "Result: PASS" not in result.output
    assert "::error title=CommitGuard could not verify repository policy::" in result.stdout


def test_malicious_yaml_in_trusted_policy_is_not_executed(hub, run_ci, gh, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    marker = tmp_path / "yaml-pwned"
    evil = f'version: 1\npolicies: !!python/object/apply:os.system ["touch {marker}"]\n'
    hub.dev.commit("chore\n", files={".commitguard.yaml": evil})
    hub.dev.push("main")
    base, shas = feature(hub, [CLEAN.format(1)])
    result = run_ci(hub.ci_clone(checkout=shas[-1]), "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 2
    assert not marker.exists()


# --------------------------------------------------------------------------- #
# Push events
# --------------------------------------------------------------------------- #
def test_push_event_scans_before_to_after(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    before = hub.dev.git("rev-parse", "main")
    hub.dev.commit(CLEAN.format(1))
    after = hub.dev.commit(gh.AI)
    hub.dev.push("main")
    ci = hub.ci_clone(checkout=after)
    result = run_ci(ci, "push", gh.push_event(before, after))
    assert result.returncode == 1
    assert "commits scanned: 2" in result.stdout
    assert f"Policy: commit before the push ({before[:12]})" in result.stdout


def test_new_branch_push_excludes_default_branch(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    for i in range(3):
        hub.dev.commit(CLEAN.format(f"main {i}"))
    hub.dev.push("main")
    _, shas = feature(hub, [CLEAN.format("new")])
    ci = hub.ci_clone(checkout=shas[-1])
    result = run_ci(ci, "push", gh.push_event(gh.ZERO, shas[-1], "refs/heads/feature"))
    assert result.returncode == 0, result.output
    assert "commits scanned: 1" in result.stdout
    assert "Policy: default branch" in result.stdout


def test_new_branch_push_with_violation_fails(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    _, shas = feature(hub, [gh.AI])
    ci = hub.ci_clone(checkout=shas[-1])
    result = run_ci(ci, "push", gh.push_event(gh.ZERO, shas[-1], "refs/heads/feature"))
    assert result.returncode == 1


def test_initial_push_uses_builtin_defaults_and_is_bounded(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    head = hub.dev.git("rev-parse", "main")
    ci = hub.ci_clone(checkout=head)
    result = run_ci(ci, "push", gh.push_event(gh.ZERO, head))
    assert result.returncode == 0, result.output
    assert "built-in defaults" in result.stdout
    hub.dev.commit(CLEAN.format(2))
    hub.dev.push("main")
    head2 = hub.dev.git("rev-parse", "main")
    ci2 = hub.ci_clone(checkout=head2, name="ci2")
    over = run_ci(ci2, "push", gh.push_event(gh.ZERO, head2), "--max-commits", "1")
    assert over.returncode == 2
    assert "more than 1 commits" in over.stdout


def test_deleted_branch_push_passes_without_scanning(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    _, shas = feature(hub, [gh.AI])
    hub.dev.git("push", "--quiet", "--no-verify", "origin", "--delete", "feature")
    ci = hub.ci_clone()
    result = run_ci(ci, "push", gh.push_event(shas[-1], gh.ZERO, "refs/heads/feature"))
    assert result.returncode == 0, result.output
    assert "commits scanned: 0" in result.stdout


def test_force_push_with_unavailable_before_commit(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    _, shas = feature(hub, [CLEAN.format("old")])
    gone = shas[-1]
    hub.dev.git("checkout", "--quiet", "feature")
    hub.dev.git("reset", "--quiet", "--hard", "main")
    new = hub.dev.commit(gh.AI)
    hub.dev.push("--force", "feature")
    hub.dev.git("checkout", "--quiet", "main")
    hub.dev.git("branch", "-D", "feature")
    hub.dev.git("reflog", "expire", "--expire=now", "--all")
    subprocess.run(["git", "gc", "--quiet", "--prune=now"], cwd=hub.bare, check=True)
    ci = hub.ci_clone(checkout=new)
    assert subprocess.run(["git", "cat-file", "-e", gone], cwd=ci.path).returncode != 0
    result = run_ci(ci, "push", gh.push_event(gone, new, "refs/heads/feature"))
    assert result.returncode == 1, result.output
    assert "force push?" in result.stdout


def test_annotated_tag_push_is_peeled(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    _, shas = feature(hub, [gh.AI])
    hub.dev.git("tag", "-a", "v1.0.0", "-m", "release", shas[-1])
    hub.dev.push("v1.0.0")
    tag = hub.dev.git("rev-parse", "v1.0.0")
    assert tag != shas[-1]
    ci = hub.ci_clone(checkout=shas[-1])
    result = run_ci(ci, "push", gh.push_event(gh.ZERO, tag, "refs/tags/v1.0.0"))
    assert result.returncode == 1, result.output


def test_merge_group_event(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format(1), gh.AI])
    ci = hub.ci_clone(checkout=shas[-1])
    payload = {
        "merge_group": {"base_sha": base, "head_sha": shas[-1], "base_ref": "refs/heads/main"},
        "repository": {"full_name": "octo/project", "default_branch": "main"},
    }
    result = run_ci(ci, "merge_group", payload)
    assert result.returncode == 1
    assert "merge queue base" in result.stdout


# --------------------------------------------------------------------------- #
# Consistency with local scanning
# --------------------------------------------------------------------------- #
def test_ci_and_local_scan_agree(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format(1), gh.AI, "x\n\nCo-authored-by: John\n"])
    ci = hub.ci_clone(checkout=shas[-1])
    ci_data = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]), "--format", "json").json()
    local = subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", "scan", f"{base}..{shas[-1]}", "-f", "json"],
        cwd=ci.path,
        capture_output=True,
        text=True,
        check=False,
    )
    local_data = json.loads(local.stdout)

    def essence(data):  # type: ignore[no-untyped-def]
        return [
            (c["commit_sha"], c["action"], sorted(f["fingerprint"] for f in c["findings"]))
            for c in data["commits"]
        ]

    assert essence(ci_data) == essence(local_data)
    assert ci_data["action"] == local_data["action"] == "block"


# --------------------------------------------------------------------------- #
# Failure modes (fail closed)
# --------------------------------------------------------------------------- #
def test_shallow_checkout_fails_with_actionable_message(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format(1), CLEAN.format(2)])
    hub.dev.git("checkout", "--quiet", "feature")
    hub.dev.push("feature:main", "--force")  # the shallow clone's HEAD is the head commit
    ci = hub.ci_clone(depth=1)
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]))
    assert result.returncode == 2
    assert "fetch-depth: 0" in result.stdout


@pytest.mark.parametrize(
    ("event_name", "raw"),
    [
        ("pull_request", "{not json"),
        ("pull_request", "[]"),
        ("pull_request", json.dumps({"pull_request": {"number": 1}})),
        (
            "pull_request",
            json.dumps(
                {"pull_request": {"number": 1, "base": {"sha": "HEAD"}, "head": {"sha": "a" * 40}}}
            ),
        ),
        (
            "push",
            json.dumps({"ref": "refs/heads/main", "before": "0" * 40, "after": "--output=/tmp/x"}),
        ),
        (
            "push",
            json.dumps({"ref": f"refs/heads/ma{ESC}in", "before": "0" * 40, "after": "a" * 40}),
        ),
        ("pull_request_target", "{}"),
        ("workflow_dispatch", "{}"),
    ],
)
def test_malformed_or_unsupported_events_fail(hub, run_ci, event_name: str, raw: str) -> None:  # type: ignore[no-untyped-def]
    result = run_ci(hub.ci_clone(), event_name, None, raw_payload=raw)
    assert result.returncode == 2, result.output
    assert "Result: FAILED" in result.stdout


def test_unknown_commit_sha_fails(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    result = run_ci(hub.ci_clone(), "pull_request", gh.pr_event(base, "d" * 40))
    assert result.returncode == 2
    assert "not available in this clone" in result.stdout


def test_missing_event_payload_fails(hub, run_ci) -> None:  # type: ignore[no-untyped-def]
    result = run_ci(hub.ci_clone(), "pull_request", {}, extra_env={"GITHUB_EVENT_PATH": ""})
    assert result.returncode == 2


def test_git_unavailable_fails(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base, shas = feature(hub, [CLEAN.format(1)])
    ci = hub.ci_clone(checkout=shas[-1])
    no_git = {"PATH": str(ci.path / "no-bin")}
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]), extra_env=no_git)
    assert result.returncode == 2
    assert "git executable not found" in result.stdout


def test_commitguard_import_failure_fails(hub, run_ci, gh, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    broken = tmp_path / "broken-site"
    (broken / "commitguard").mkdir(parents=True)
    (broken / "commitguard" / "__init__.py").write_text("raise ImportError('simulated')\n")
    base, shas = feature(hub, [CLEAN.format(1)])
    ci = hub.ci_clone(checkout=shas[-1])
    env = {"PYTHONPATH": str(broken)}
    result = run_ci(ci, "pull_request", gh.pr_event(base, shas[-1]), extra_env=env)
    assert result.returncode != 0
    assert "simulated" in result.stderr
    assert "Result: PASS" not in result.output
