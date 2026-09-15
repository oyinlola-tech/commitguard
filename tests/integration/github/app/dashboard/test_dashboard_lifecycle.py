"""The primary Phase 6 scenario and the violation lifecycle, through the real stack.

GitHub webhook -> App -> real Git mirror -> Detection + Policy engine -> Check Run
                                                   -> findings / violations -> dashboard API
"""

from commitguard.audit.models import AuditEventType

ALICE = (501, "alice")
BOB = (601, "bob")
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


def _violations(browser, **query):  # type: ignore[no-untyped-def]
    result = browser.get("/api/v1/violations", **query)
    assert result.status == 200, result.raw
    return result.data


def test_primary_end_to_end_scenario(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    # 1-4. Authenticate, GitHub connected, repository available and synchronized.
    alice = dash.sign_in(ALICE)
    installations = alice.get("/api/v1/github/installations").data
    assert [(i["id"], i["status"]) for i in installations] == [(42, "connected")]
    sync = alice.post("/api/v1/github/installations/42/sync")
    assert sync.status == 200, sync.raw
    assert sync.data["repositories"] == 1
    repositories = alice.get("/api/v1/repositories").data
    assert [r["full_name"] for r in repositories] == ["octo-org/project"]
    assert repositories[0]["default_branch"] == "main"
    assert repositories[0]["protection"] == "unknown"  # never "protected" without evidence

    # 5-6. A clean commit is scanned and the dashboard shows PASS.
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    clean = hub.dev.commit("feat: clean change\n", files={"a.txt": "1\n"})
    hub.dev.push("feature")
    ops.pull_request(base, clean, action="opened")
    scans = alice.get("/api/v1/scans").data
    assert [(s["head_sha"], s["result"]) for s in scans] == [(clean, "pass")]
    assert dash.github.runs_for(clean)[-1]["conclusion"] == "success"

    # 7-11. An AI-attributed commit: webhook, scan, failing GitHub check, BLOCKED scan.
    ai = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, ai)
    assert dash.github.runs_for(ai)[-1]["conclusion"] == "failure"
    scans = alice.get("/api/v1/scans").data
    blocked = scans[0]
    assert (blocked["head_sha"], blocked["result"]) == (ai, "blocked")
    assert blocked["commits_scanned"] == 2
    assert blocked["violations"] == 1
    detail = alice.get(f"/api/v1/scans/{blocked['id']}").data
    assert detail["scan"]["result"] == "blocked"
    assert detail["conclusion"] == "failure"
    assert detail["rules_version"]
    assert detail["policy_version"]
    assert detail["tool_version"]
    [finding] = detail["findings"]
    assert (finding["rule_id"], finding["action"], finding["severity"]) == (
        "ai_coauthor",
        "block",
        "high",
    )
    assert finding["evidence"][0]["value"] == "Claude <noreply@anthropic.com>"
    assert finding["author"] == "Test Author <author@example.com>"

    # 12-14. The violation appears; opening it shows evidence and remediation.
    [violation] = _violations(alice)
    assert (violation["rule_id"], violation["status"], violation["commit_sha"]) == (
        "ai_coauthor",
        "open",
        ai,
    )
    overview = alice.get("/api/v1/dashboard/overview").data
    assert overview["summary"]["open_violations"] == 1
    assert overview["summary"]["scans_blocked"] == 1
    info = alice.get(f"/api/v1/violations/{violation['id']}").data
    assert info["violation"]["status"] == "open"
    assert info["evidence"][0]["source"] == "coauthor_trailer"
    assert any("rewrite" in step.lower() for step in info["recommended_steps"])
    assert info["recommended_steps"][-1].startswith("CommitGuard does not rewrite Git history")
    assert [e["label"] for e in info["exposures"] if e["active"]] == ["#7"]

    # 15-17. The commit is corrected; the new scan passes and the dashboard updates.
    hub.dev.git("reset", "-q", "--hard", clean)
    fixed = hub.dev.commit("feat: add payment service\n")
    hub.dev.push("--force", "feature")
    ops.pull_request(base, fixed)
    assert dash.github.runs_for(fixed)[-1]["conclusion"] == "success"
    assert alice.get("/api/v1/scans").data[0]["result"] == "pass"
    assert _violations(alice, status="open") == []
    [resolved] = _violations(alice, status="resolved")
    assert resolved["id"] == violation["id"]
    overview = alice.get("/api/v1/dashboard/overview").data
    assert overview["summary"]["open_violations"] == 0

    # 18. History remains auditable: detections, the blocked scan and audit events.
    info = alice.get(f"/api/v1/violations/{violation['id']}").data
    assert "no longer part of pull request #7" in info["resolution"]
    assert [d["result"] for d in info["detections"]] == ["blocked"]
    assert alice.get(f"/api/v1/scans/{blocked['id']}").data["scan"]["result"] == "blocked"
    types = {e["type"] for e in alice.get("/api/v1/audit", limit=100).data}
    for expected in (
        AuditEventType.INSTALLATION_CREATED,
        AuditEventType.REPOSITORIES_SYNCED,
        AuditEventType.SCAN_FAILED,
        AuditEventType.VIOLATION_OPENED,
        AuditEventType.VIOLATION_RESOLVED,
        AuditEventType.USER_SIGNED_IN,
    ):
        assert expected.value in types

    # The other tenant saw none of it.
    bob = dash.sign_in(BOB)
    assert bob.get("/api/v1/scans").data == []
    assert _violations(bob) == []


def test_running_scan_is_not_cached(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, head, run=False)
    [queued] = alice.get("/api/v1/scans").data
    assert queued["result"] == "queued"
    detail = alice.get(f"/api/v1/scans/{queued['id']}")
    assert detail.header("Cache-Control") == "no-store"
    dash.app.run()
    assert alice.get(f"/api/v1/scans/{queued['id']}").data["scan"]["result"] == "blocked"


def test_acknowledgement_does_not_change_enforcement(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    sam = dash.sign_in((503, "sam"))
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, head)
    [violation] = _violations(sam)
    acknowledged = sam.put(
        f"/api/v1/violations/{violation['id']}/acknowledgement", {"note": "reviewing with team"}
    )
    assert acknowledged.status == 200, acknowledged.raw
    assert acknowledged.data["violation"]["status"] == "acknowledged"
    assert acknowledged.data["acknowledgement"]["by"] == "sam"
    # The GitHub check still fails and a re-scan still blocks.
    assert dash.github.runs_for(head)[-1]["conclusion"] == "failure"
    [scan] = sam.get("/api/v1/scans").data
    rescan = sam.post(f"/api/v1/scans/{scan['id']}/rescan")
    assert rescan.status == 202, rescan.raw
    dash.app.run()
    latest = sam.get("/api/v1/scans").data[0]
    assert (latest["id"], latest["result"], latest["requested_by"]) == (
        rescan.data["scan"],
        "blocked",
        "sam",
    )
    assert dash.github.runs_for(head)[-1]["conclusion"] == "failure"
    assert _violations(sam, status="acknowledged")[0]["id"] == violation["id"]
    # There is no way to mark a violation resolved by hand.
    assert sam.put(f"/api/v1/violations/{violation['id']}", {"status": "resolved"}).status == 405
    removed = sam.delete(f"/api/v1/violations/{violation['id']}/acknowledgement")
    assert removed.data["violation"]["status"] == "open"
    types = [e["type"] for e in sam.get("/api/v1/audit", limit=100).data]
    assert "violation_acknowledged" in types
    assert "violation_acknowledgement_removed" in types
    assert "scan_requested" in types


def test_closed_and_merged_pull_requests(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, head)
    [violation] = _violations(alice)

    # Closed without merging: no longer present anywhere CommitGuard monitors.
    assert ops.close_pull_request(base, head).status == 200
    info = alice.get(f"/api/v1/violations/{violation['id']}").data
    assert info["violation"]["status"] == "resolved"
    assert info["resolution"] == "pull request #7 was closed without merging"

    # Reopened: the scan detects it again; the violation reopens.
    ops.pull_request(base, head, action="reopened")
    assert alice.get(f"/api/v1/violations/{violation['id']}").data["violation"]["status"] == "open"

    # Merged: the violation moves to the base branch and stays open.
    ops.close_pull_request(base, head, merged=True)
    info = alice.get(f"/api/v1/violations/{violation['id']}").data
    assert info["violation"]["status"] == "open"
    active = [e for e in info["exposures"] if e["active"]]
    assert [(e["kind"], e["label"]) for e in active] == [("branch", "refs/heads/main")]


def test_branch_history_rewrite_and_deletion_resolve(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    before = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "topic")
    bad = hub.dev.commit(AI)
    hub.dev.push("topic")
    zero = "0" * 40
    ops.push(zero, bad, ref="refs/heads/topic")
    [violation] = _violations(alice)
    assert violation["status"] == "open"

    # An incremental push that keeps the commit does not resolve it.
    later = hub.dev.commit("docs: more\n")
    hub.dev.push("topic")
    ops.push(bad, later, ref="refs/heads/topic")
    assert _violations(alice)[0]["status"] == "open"

    # Rewriting history so the commit is gone resolves it.
    hub.dev.git("reset", "-q", "--hard", before)
    rewritten = hub.dev.commit("feat: add payment service\n")
    hub.dev.push("--force", "topic")
    ops.push(later, rewritten, ref="refs/heads/topic")
    info = alice.get(f"/api/v1/violations/{violation['id']}").data
    assert info["violation"]["status"] == "resolved"
    assert "no longer reachable from refs/heads/topic" in info["resolution"]

    # A second violation on another branch is resolved when that branch is deleted.
    hub.dev.git("checkout", "-q", "-b", "throwaway", before)
    other = hub.dev.commit("chore: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    hub.dev.push("throwaway")
    ops.push(zero, other, ref="refs/heads/throwaway")
    [second] = _violations(alice, status="open")
    ops.push(other, zero, ref="refs/heads/throwaway")
    info = alice.get(f"/api/v1/violations/{second['id']}").data
    assert info["violation"]["status"] == "resolved"
    assert info["resolution"] == "branch refs/heads/throwaway was deleted"


def test_stale_scan_does_not_reopen_resolved_violation(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    bad = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, bad, run=False)  # old event, still queued
    hub.dev.git("reset", "-q", "--hard", base)
    good = hub.dev.commit("feat: clean\n")
    hub.dev.push("--force", "feature")
    ops.pull_request(base, good, run=False)
    jobs = sorted(dash.app.jobs(), key=lambda j: j.sequence)
    worker = dash.app.service.worker
    assert worker.process(jobs[1].job_id).value == "passed"  # newer scan finishes first
    # The older scan now runs; the worker cancels it as superseded.
    assert worker.process(jobs[0].job_id).value == "cancelled"
    assert _violations(alice) == []
    results = {s["head_sha"]: s["result"] for s in alice.get("/api/v1/scans").data}
    assert results == {good: "pass", bad: "cancelled"}
