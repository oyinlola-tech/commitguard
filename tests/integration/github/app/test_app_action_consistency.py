"""The GitHub App and the GitHub Action reach the same decision for the same commits.

Both run ``plan_ci`` + ``execute_ci_plan`` through ``ScanService``; this test
checks that the App's metadata-only mirror and the Action's full checkout do
not change the outcome.
"""

import pytest

from commitguard.github.checks import APP_PUSH_CHECK_NAME

ZERO = "0" * 40
WEAKEN = "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"
BOT = "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("clean", "allow"),
        ("ai_coauthor", "block"),
        ("bot_warning", "warn"),
        ("policy_weakening", "block"),
        ("multiple_violations", "block"),
    ],
)
def test_pull_request_decisions_match(scenario: str, expected: str, app, hub, gh, run_ci) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-b", "feature")
    if scenario == "clean":
        hub.dev.commit("feat: clean\n", files={"x.txt": "x\n"})
    elif scenario == "ai_coauthor":
        hub.dev.commit(gh.AI)
    elif scenario == "bot_warning":
        hub.dev.commit("chore: bump\n", author=BOT)
    elif scenario == "policy_weakening":
        hub.dev.commit(gh.AI, files={".commitguard.yaml": WEAKEN})
    else:
        hub.dev.commit(gh.AI)
        hub.dev.commit("fix: y\n\nGenerated-by: Claude Code\n")
    head = hub.dev.git("rev-parse", "HEAD")
    hub.dev.push("feature")

    action = run_ci(
        hub.ci_clone(checkout=head), "pull_request", gh.pr_event(base, head), "--format", "json"
    )
    action_report = action.json()

    app.deliver("pull_request", app.open_pull_request(base, head))
    assert app.run() == 1
    job = app.jobs()[0]

    assert action_report["action"] == expected
    assert job.result_action == expected
    assert job.commits_scanned == len(action_report["commits"])
    assert job.violations == sum(1 for c in action_report["commits"] if c["action"] == "block")
    assert job.warnings == sum(1 for c in action_report["commits"] if c["action"] == "warn")
    conclusion = app.latest_run(head)["conclusion"]
    assert conclusion == ("failure" if action.returncode == 1 else "success")


def test_push_decisions_match(app, hub, gh, run_ci) -> None:  # type: ignore[no-untyped-def]
    app.install()
    before = hub.dev.git("rev-parse", "main")
    hub.dev.commit("feat: ok\n")
    after = hub.dev.commit(gh.AI)
    hub.dev.push("main")
    action = run_ci(
        hub.ci_clone(checkout=after), "push", gh.push_event(before, after), "--format", "json"
    )
    app.deliver(
        "push",
        {
            **gh.push_event(before, after),
            "repository": {"id": 5001, "full_name": "octo-org/project", "default_branch": "main"},
            "installation": {"id": 42},
        },
    )
    app.run()
    job = app.jobs()[0]
    assert (action.json()["action"], job.result_action) == ("block", "block")
    assert job.commits_scanned == len(action.json()["commits"]) == 2
    assert app.latest_run(after, APP_PUSH_CHECK_NAME)["conclusion"] == "failure"
