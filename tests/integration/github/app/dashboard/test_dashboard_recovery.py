"""Policy recovery, check executions and the merge queue through the dashboard API.

Includes the complete Phase 7 lifecycle (spec step list in the scenario test).
"""

import sqlite3

import pytest

from commitguard.audit.models import AuditEvent, AuditEventType
from commitguard.notifications.settings import NotificationMode, NotificationSettings
from commitguard.security.hashing import sha256_hex
from commitguard.security.secrets import Secret

ALICE = (501, "alice")  # owner
VICTOR = (502, "victor")  # viewer
SAM = (503, "sam")  # security manager
ADA = (504, "ada")  # admin
BOB = (601, "bob")  # owner of globex
ORG = 1001
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
ALLOW_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"


def _save(browser, version, floors, **extra):  # type: ignore[no-untyped-def]
    result = browser.put(
        f"/api/v1/policies/{ORG}", {"expected_version": version, "floors": floors, **extra}
    )
    return result


def _rollback(browser, target, expected, *, reason="restore stable policy", confirm=True, org=ORG):  # type: ignore[no-untyped-def]
    body = {"target_version": target, "expected_current_version": expected, "confirm": confirm}
    if reason is not None:
        body["reason"] = reason
    return browser.post(f"/api/v1/policies/{org}/rollback", body)


def _org_events(env, event_type):  # type: ignore[no-untyped-def]
    rows = env.store.query(
        "SELECT document FROM audit_events WHERE account_id = ? AND type = ? ORDER BY occurred_at",
        (ORG, event_type.value),
    )
    return [AuditEvent.model_validate_json(r["document"]) for r in rows]


@pytest.fixture
def permissive(hub):  # type: ignore[no-untyped-def]
    """The trusted base allows ai_coauthor, so only the organization floor can block."""
    hub.dev.commit("chore: relax policy\n", files={".commitguard.yaml": ALLOW_CONFIG})
    hub.dev.push("main")
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    return base, head


# --------------------------------------------------------------------------- #
# Policy rollback
# --------------------------------------------------------------------------- #
def test_rollback_publishes_a_new_version_and_preserves_history(dash, ops, permissive) -> None:  # type: ignore[no-untyped-def]
    base, head = permissive
    alice, ada = dash.sign_in(ALICE), dash.sign_in(ADA)
    assert _save(alice, 0, {"ai_coauthor": "block"}, reason="v1").status == 200
    assert (
        _save(alice, 1, {"ai_coauthor": "block", "bot_identity": "warn"}, reason="v2").status == 200
    )
    weakening = _save(
        alice, 2, {"bot_identity": "block"}, reason="v3 drops ai floor", confirm_weakening=True
    )
    assert weakening.status == 200, weakening.raw

    ops.pull_request(base, head, action="opened")  # scanned under v3: the AI commit passes
    [v3_scan] = alice.get("/api/v1/scans").data
    assert v3_scan["result"] == "pass"
    assert alice.get(f"/api/v1/scans/{v3_scan['id']}").data["organization_policy_version"] == 3

    preview = alice.get(f"/api/v1/policies/{ORG}/diff", **{"from": 3, "to": 2}).data
    assert [e["policy_id"] for e in preview["added"]] == ["ai_coauthor"]
    assert [(e["policy_id"], e["old"], e["new"]) for e in preview["changed"]] == [
        ("bot_identity", "block", "warn")
    ]
    assert preview["weakening"] is True

    rolled = _rollback(ada, 2, 3, reason="v3 removed the AI floor by mistake")
    assert rolled.status == 200, rolled.raw
    assert rolled.data["version"] == 4
    assert rolled.meta["rollback"] == {
        **rolled.meta["rollback"],
        "new_version": 4,
        "restored_version": 2,
        "rollback_of": 3,
    }
    rule = next(r for r in rolled.data["rules"] if r["policy_id"] == "ai_coauthor")
    assert rule["organization_floor"] == "block"

    versions = alice.get(f"/api/v1/policies/{ORG}/versions").data
    assert [(v["version"], v["status"], v["kind"]) for v in versions] == [
        (4, "active", "rollback"),
        (3, "archived", "change"),
        (2, "archived", "change"),
        (1, "archived", "change"),
    ]
    assert (versions[0]["rollback_of"], versions[0]["restored_version"]) == (3, 2)
    assert versions[0]["fingerprint"] == versions[2]["fingerprint"]  # same document as v2
    assert versions[1]["floors"] == {"bot_identity": "block"}  # v3 is untouched

    [event] = _org_events(dash, AuditEventType.ORGANIZATION_POLICY_ROLLED_BACK)
    assert (event.actor_login, event.request_id is not None) == ("ada", True)
    assert {
        k: event.data[k] for k in ("previous_version", "target_version", "new_version", "reason")
    } == {
        "previous_version": 3,
        "target_version": 2,
        "new_version": 4,
        "reason": "v3 removed the AI floor by mistake",
    }
    dash.app.service.notifications.run_once()
    [note] = [
        n for n in alice.get("/api/v1/notifications").data if n["type"] == "policy_rolled_back"
    ]
    assert "restores v2" in note["title"]
    [notification_row] = dash.store.query(
        "SELECT request_id FROM notification_events WHERE type = 'policy_rolled_back'"
    )
    assert notification_row["request_id"] == event.request_id  # correlated with the API request

    # Existing violations are not resolved by a rollback; future scans use v4.
    rescan = alice.post(f"/api/v1/scans/{v3_scan['id']}/rescan")
    assert rescan.status == 202, rescan.raw
    dash.app.run()
    new_scan = alice.get(f"/api/v1/scans/{rescan.data['scan']}").data
    assert (new_scan["scan"]["result"], new_scan["organization_policy_version"]) == ("blocked", 4)
    historical = alice.get(f"/api/v1/scans/{v3_scan['id']}").data
    assert (historical["scan"]["result"], historical["organization_policy_version"]) == ("pass", 3)
    assert dash.github.runs_for(head)[-1]["conclusion"] == "failure"
    assert dash.app.service.metrics.value("policy_rollbacks") == 1


def test_rollback_is_authorized_validated_and_atomic(dash) -> None:  # type: ignore[no-untyped-def]
    from commitguard.security.rate_limit import RequestRateLimiter

    dash.api._limiters["sensitive"] = RequestRateLimiter(1000)  # limits are tested separately
    alice, victor, sam, bob = (dash.sign_in(u) for u in (ALICE, VICTOR, SAM, BOB))
    for version, floors in enumerate(({"ai_coauthor": "block"}, {"ai_coauthor": "warn"}), start=0):
        assert _save(alice, version, floors, reason="x", confirm_weakening=True).status == 200

    assert _rollback(victor, 1, 2).status == 403
    assert _rollback(sam, 1, 2).status == 403  # security managers cannot change policy
    assert _rollback(bob, 1, 2).status == 404  # cross-tenant: indistinguishable from missing
    assert _rollback(bob, 1, 0, org=2002).status == 400  # bob's own org has no version 1

    missing_reason = _rollback(alice, 1, 2, reason=None)
    assert (missing_reason.status, missing_reason.error["field"]) == (400, "reason")
    assert _rollback(alice, 1, 2, reason="   ").status == 400
    assert _rollback(alice, 1, 2, confirm=False).error["code"] == "CONFIRMATION_REQUIRED"
    assert _rollback(alice, 9, 2).error["field"] == "target_version"
    assert _rollback(alice, 2, 2).error["field"] == "target_version"
    assert _rollback(alice, 0, 2).error["field"] == "target_version"
    assert _rollback(alice, 1, 1).error["code"] == "CONFLICT"  # stale view of the policy
    for forged in ({"target_version": "1"}, {"target_version": True}, {"target_version": -1}):
        body = {"expected_current_version": 2, "reason": "x", "confirm": True, **forged}
        assert alice.post(f"/api/v1/policies/{ORG}/rollback", body).status == 400
    get_attempt = alice.get(f"/api/v1/policies/{ORG}/rollback")
    assert get_attempt.status == 405  # never through GET
    without_csrf = alice.request(
        "POST",
        f"/api/v1/policies/{ORG}/rollback",
        body={"target_version": 1, "expected_current_version": 2, "reason": "x", "confirm": True},
        send_csrf=False,
    )
    assert without_csrf.error["code"] == "CSRF_FAILED"

    # Nothing above changed the active version or wrote a version row.
    assert alice.get(f"/api/v1/policies/{ORG}").data["version"] == 2
    assert dash.store.query("SELECT COUNT(*) AS n FROM organization_policy_versions")[0]["n"] == 2
    assert _org_events(dash, AuditEventType.ORGANIZATION_POLICY_ROLLED_BACK) == []
    assert dash.app.service.metrics.value("policy_rollback_failures") >= 8


def test_restoring_a_weaker_version_needs_a_recent_sign_in(dash, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    assert _save(alice, 0, {"ai_coauthor": "warn"}).status == 200
    assert _save(alice, 1, {"ai_coauthor": "block"}).status == 200
    clock.advance(minutes=30)
    assert _rollback(alice, 1, 2).error["code"] == "REAUTHENTICATION_REQUIRED"
    alice = dash.sign_in(ALICE)
    assert _rollback(alice, 1, 2).status == 200


def test_corrupted_or_tampered_versions_cannot_be_restored(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    store = dash.store
    # A row whose fingerprint does not match its document (tampered storage).
    with store.transaction() as db:
        db.execute(
            "INSERT INTO organization_policy_versions (account_id, version, document, fingerprint, "
            "created_at) VALUES (?, 1, ?, ?, 0)",
            (ORG, '{"ai_coauthor":"allow"}', sha256_hex(b'{"ai_coauthor":"block"}')),
        )
    assert _save(alice, 1, {"ai_coauthor": "block"}).status == 200
    corrupted = _rollback(alice, 1, 2)
    assert (corrupted.status, corrupted.error["code"]) == (422, "POLICY_VERSION_INVALID")
    assert alice.get(f"/api/v1/policies/{ORG}").data["version"] == 2

    # Published versions are immutable in the database itself.
    for statement in (
        "UPDATE organization_policy_versions SET document = '{}' WHERE version = 2",
        "DELETE FROM organization_policy_versions WHERE version = 2",
    ):
        with pytest.raises(Exception, match="state store error"), store.transaction() as db:
            db.execute(statement)
    raw = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        raw.execute("UPDATE organization_policy_versions SET reason = 'x'")
    raw.close()


def test_concurrent_rollback_and_publish_never_lose_an_update(dash) -> None:  # type: ignore[no-untyped-def]
    alice, ada = dash.sign_in(ALICE), dash.sign_in(ADA)
    assert _save(alice, 0, {"ai_coauthor": "block"}).status == 200
    assert _save(alice, 1, {"ai_coauthor": "block", "bot_identity": "block"}).status == 200
    # Both administrators loaded v2. Ada publishes first; Alice's rollback is based on v2.
    assert (
        _save(
            ada, 2, {"ai_coauthor": "block", "bot_identity": "block", "ai_identity": "block"}
        ).status
        == 200
    )
    conflict = _rollback(alice, 1, 2)
    assert (conflict.status, conflict.error["code"]) == (409, "CONFLICT")
    current = alice.get(f"/api/v1/policies/{ORG}").data
    assert current["version"] == 3
    assert (
        next(r for r in current["rules"] if r["policy_id"] == "ai_identity")["organization_floor"]
        == "block"
    )

    # The same race in the other direction: the rollback wins, the stale publish conflicts.
    assert _rollback(alice, 1, 3).status == 200
    assert (
        _save(ada, 3, {"ai_coauthor": "warn"}, confirm_weakening=True, reason="x").error["code"]
        == "CONFLICT"
    )


def test_rollback_is_rate_limited(dash, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    limiter = dash.api._limiters["sensitive"]
    allowed = [limiter.allow("user:probe") for _ in range(12)]
    assert allowed.count(True) == 10
    responses = [_rollback(alice, 1, 0).status for _ in range(12)]
    assert 429 in responses


# --------------------------------------------------------------------------- #
# Executions and merge queue views
# --------------------------------------------------------------------------- #
def test_execution_history_shows_policy_changes_between_runs(
    dash, ops, permissive, payloads
) -> None:  # type: ignore[no-untyped-def]
    base, head = permissive
    alice, bob = dash.sign_in(ALICE), dash.sign_in(BOB)
    assert _save(alice, 0, {"ai_coauthor": "block"}).status == 200  # policy v1
    ops.pull_request(base, head, action="opened")
    [original] = alice.get("/api/v1/scans").data
    assert original["result"] == "blocked"
    # Policy v2 removes the floor; GitHub "Re-run" on the failed check.
    assert _save(alice, 1, {}, confirm_weakening=True, reason="false positive").status == 200
    run = dash.github.runs_for(head)[-1]
    assert dash.app.deliver("check_run", payloads.check_run(run)).body == {"status": "queued"}
    dash.app.run()

    history = alice.get(f"/api/v1/scans/{original['id']}/executions").data
    assert [
        (e["execution"], e["trigger"], e["result"], e["organization_policy_version"], e["current"])
        for e in history["items"]
    ] == [
        (2, "rerun", "pass", 2, True),
        (1, "pull_request", "blocked", 1, False),
    ]
    assert history["policy_changed"] is True
    assert history["rules_changed"] is False
    detail = alice.get(f"/api/v1/scans/{original['id']}").data
    assert (detail["executions"], detail["latest_execution"]) == (2, history["items"][0]["id"])
    assert detail["scan"]["result"] == "blocked"  # the original execution is not rewritten
    assert detail["organization_policy_version"] == 1
    assert dash.github.runs_for(head)[-1]["conclusion"] == "success"
    assert bob.get(f"/api/v1/scans/{original['id']}/executions").status == 404


def test_merge_queue_view_is_evidence_based_and_tenant_scoped(dash, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    alice, bob = dash.sign_in(ALICE), dash.sign_in(BOB)
    unknown = alice.get("/api/v1/repositories/5001/merge-queue").data
    assert (unknown["status"], unknown["current"], unknown["permission"]) == (
        "unknown",
        None,
        "missing",
    )
    assert bob.get("/api/v1/repositories/5001/merge-queue").status == 404

    dash.github.rulesets[5001] = [{"type": "merge_queue", "parameters": {}}]
    assert alice.post("/api/v1/repositories/5001/enforcement/refresh").status == 200
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    hub.dev.commit("feat: clean\n", files={"x.txt": "1\n"})
    hub.dev.push("feature")
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    dash.app.deliver("merge_group", payloads.merge_group(queue_base, group))
    dash.app.run()
    view = alice.get("/api/v1/repositories/5001/merge-queue").data
    assert view["status"] == "enabled"
    assert view["current"]["head_sha"] == group
    assert (view["current"]["result"], view["current"]["pull_requests"]) == ("pass", [7])
    detail = alice.get("/api/v1/repositories/5001").data
    assert detail["enforcement"]["merge_queue"]["status"] == "enabled"
    [scan] = [s for s in alice.get("/api/v1/scans").data if s["event"] == "merge_group"]
    assert scan["failure_source"] == "merge_queue"
    assert alice.get(f"/api/v1/scans/{scan['id']}").data["merge_group"]["head_sha"] == group
    assert base != group


# --------------------------------------------------------------------------- #
# The complete Phase 7 lifecycle
# --------------------------------------------------------------------------- #
def test_phase7_lifecycle(make_dashboard, make_ops, hub, payloads) -> None:  # type: ignore[no-untyped-def]
    env = make_dashboard(
        notification_settings=NotificationSettings(
            mode=NotificationMode.TEST, signing_key=Secret("k" * 40), production=False
        )
    )
    ops = make_ops(env)
    alice, ada = env.sign_in(ALICE), env.sign_in(ADA)
    notifications = env.app.service.notifications

    # 1. Repository connected; policy v10 (built up as ten published versions).
    for version in range(10):
        floors = (
            {"ai_coauthor": "block"}
            if version % 2 == 0
            else {"ai_coauthor": "block", "bot_identity": "warn"}
        )
        assert (
            _save(ada, version, floors, reason=f"v{version + 1}", confirm_weakening=True).status
            == 200
        )
    assert alice.get("/api/v1/github/installations").data[0]["status"] == "connected"
    settings = ada.get(f"/api/v1/organizations/{ORG}/notification-settings").data
    document = {t["type"]: dict(t["organization"]) for t in settings["types"]}
    document["high_violation"]["email"] = True
    assert (
        ada.put(
            f"/api/v1/organizations/{ORG}/notification-settings",
            {
                "expected_version": 0,
                "types": document,
                "email_recipients": ["security@example.com"],
            },
        ).status
        == 200
    )
    notifications.run_once()

    # 2. A pull request contains an AI attribution: BLOCK.
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    ai = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, ai, action="opened")
    assert env.github.runs_for(ai)[-1]["conclusion"] == "failure"

    # 3. Notification generated: dashboard, e-mail queued, audit recorded.
    notifications.dispatcher.dispatch_pending()
    [note] = [n for n in alice.get("/api/v1/notifications").data if n["type"] == "high_violation"]
    deliveries = ada.get(f"/api/v1/organizations/{ORG}/notification-deliveries", limit=100).data
    [queued] = [d for d in deliveries if d["notification_type"] == "high_violation"]
    assert (queued["channel"], queued["status"]) == ("email", "pending")
    audit_types = {r["type"] for r in env.store.query("SELECT type FROM audit_events")}
    assert {"notification_created", "violation_opened", "scan_failed"} <= audit_types
    notifications.worker.deliver_due()
    assert any(
        m.email.to == "security@example.com" and "ai_coauthor" in m.email.subject
        for m in notifications.email.sent
    )

    # 4-5. The developer fixes the commit; GitHub re-runs the check: execution #2 PASS.
    hub.dev.git("reset", "-q", "--hard", base)
    fixed = hub.dev.commit("feat: add payment service\n")
    hub.dev.push("--force", "feature")
    env.github.pulls[(5001, 7)]["head"]["sha"] = fixed
    ops.pull_request(base, fixed)
    first = env.github.runs_for(fixed)[-1]
    assert first["conclusion"] == "success"
    env.app.deliver("check_run", payloads.check_run(first))
    env.app.run()
    fixed_scans = [s for s in alice.get("/api/v1/scans").data if s["head_sha"] == fixed]
    [fixed_scan] = [s for s in fixed_scans if s["trigger"] == "pull_request"]
    executions = alice.get(f"/api/v1/scans/{fixed_scan['id']}/executions").data["items"]
    assert [(e["execution"], e["trigger"], e["result"]) for e in executions] == [
        (2, "rerun", "pass"),
        (1, "pull_request", "pass"),
    ]

    # 6-8. The pull request enters the merge queue; the merge group is scanned and passes.
    queue_base, group = payloads.push_merge_group(hub, "main", "feature")
    env.app.deliver("merge_group", payloads.merge_group(queue_base, group))
    env.app.run()
    assert env.github.runs_for(group)[-1]["conclusion"] == "success"
    [group_scan] = [s for s in alice.get("/api/v1/scans").data if s["event"] == "merge_group"]
    assert group_scan["result"] == "pass"
    assert group_scan["organization_id"] == ORG

    # 9. An administrator publishes a problematic policy v11 (drops the AI floor).
    bad = _save(ada, 10, {"bot_identity": "warn"}, confirm_weakening=True, reason="cleanup")
    assert bad.status == 200
    assert bad.data["version"] == 11
    hub.dev.commit(AI)
    hub.dev.push("feature")
    ai_again = hub.dev.git("rev-parse", "HEAD")
    env.github.pulls[(5001, 7)]["head"]["sha"] = ai_again
    ops.pull_request(base, ai_again)
    [v11_scan] = [s for s in alice.get("/api/v1/scans").data if s["head_sha"] == ai_again]
    v11_detail = alice.get(f"/api/v1/scans/{v11_scan['id']}").data
    assert v11_detail["organization_policy_version"] == 11
    # The repository's own configuration still blocks it; the floor change is what is audited.

    # 10-11. Rollback v11 -> v10: audit, notification, new effective version.
    rolled = _rollback(ada, 10, 11, reason="v11 removed the AI coauthor floor")
    assert rolled.status == 200, rolled.raw
    assert (
        rolled.meta["rollback"]["new_version"],
        rolled.meta["rollback"]["restored_version"],
    ) == (12, 10)
    assert _org_events(env, AuditEventType.ORGANIZATION_POLICY_ROLLED_BACK)
    notifications.run_once()
    assert any(n["type"] == "policy_rolled_back" for n in alice.get("/api/v1/notifications").data)

    # 12. Old scans still show v11; new scans use the restored v10 document (v12).
    rescan = alice.post(f"/api/v1/scans/{v11_scan['id']}/rescan")
    assert rescan.status == 202, rescan.raw
    env.app.run()
    assert alice.get(f"/api/v1/scans/{v11_scan['id']}").data["organization_policy_version"] == 11
    new_detail = alice.get(f"/api/v1/scans/{rescan.data['scan']}").data
    assert new_detail["organization_policy_version"] == 12
    effective = {p["id"]: p["action"] for p in new_detail["effective_policies"]}
    assert effective["ai_coauthor"] == "block"
    versions = alice.get(f"/api/v1/policies/{ORG}/versions", limit=3).data
    assert [(v["version"], v["kind"], v["restored_version"]) for v in versions] == [
        (12, "rollback", 10),
        (11, "change", None),
        (10, "change", None),
    ]
    assert note["link"].startswith("/violations/")
