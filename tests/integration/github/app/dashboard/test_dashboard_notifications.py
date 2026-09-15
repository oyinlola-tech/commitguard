"""Notifications through the real stack: outbox, preferences, in-app inbox, e-mail and webhooks."""

import json

import pytest

from commitguard.audit.models import AuditEventType
from commitguard.core.result import Severity
from commitguard.notifications.channels.base import DeliveryError
from commitguard.notifications.channels.webhook import verify_signature
from commitguard.notifications.models import NotificationEvent, NotificationType
from commitguard.notifications.outbox import emit
from commitguard.notifications.settings import NotificationMode, NotificationSettings
from commitguard.security.secrets import Secret

ALICE = (501, "alice")  # owner
VICTOR = (502, "victor")  # viewer
SAM = (503, "sam")  # security manager
ADA = (504, "ada")  # admin
BOB = (601, "bob")  # owner of globex
ORG = 1001
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
AI_2 = "feat: add refunds\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
ORIGIN = "https://commitguard.test"


@pytest.fixture
def ndash(make_dashboard):  # type: ignore[no-untyped-def]
    settings = NotificationSettings(
        mode=NotificationMode.TEST,
        signing_key=Secret("n" * 48),
        dashboard_origin=ORIGIN,
        production=False,
    )
    return make_dashboard(notification_settings=settings)


@pytest.fixture
def blocked(ndash, hub, make_ops):  # type: ignore[no-untyped-def]
    """Open a pull request whose commits (default: one AI-attributed commit) are blocked."""

    def factory(*messages: str) -> tuple[str, str]:
        ops = make_ops(ndash)
        base = hub.dev.git("rev-parse", "main")
        hub.dev.git("checkout", "-q", "-B", "feature", base)
        head = base
        for message in messages or (AI,):
            head = hub.dev.commit(message)
        hub.dev.push("--force", "feature")
        ops.pull_request(base, head, action="opened")
        return base, head

    return factory


def _run(env):  # type: ignore[no-untyped-def]
    return env.app.service.notifications.run_once()


def _inbox(browser, **query):  # type: ignore[no-untyped-def]
    result = browser.get("/api/v1/notifications", **query)
    assert result.status == 200, result.raw
    return result.data


def _enable(browser, org, types, **extra):  # type: ignore[no-untyped-def]
    current = browser.get(f"/api/v1/organizations/{org}/notification-settings").data
    document = {t["type"]: dict(t["organization"]) for t in current["types"]}
    for name, channels in types.items():
        document[name].update(channels)
    return browser.put(
        f"/api/v1/organizations/{org}/notification-settings",
        {"expected_version": current["version"], "types": document, **extra},
    )


# --------------------------------------------------------------------------- #
# Generation, deduplication, recipients
# --------------------------------------------------------------------------- #
def test_blocked_violation_notifies_permitted_members_once(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    alice, victor, bob = ndash.sign_in(ALICE), ndash.sign_in(VICTOR), ndash.sign_in(BOB)
    # One pull request, three AI-attributed commits: one notification, not three.
    blocked(AI, AI_2, "feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    assert _run(ndash).dispatched == 1
    [note] = _inbox(alice)
    assert note["type"] == "high_violation"
    assert note["severity"] == "high"
    assert note["state"] == "unread"
    assert "3 commit(s)" in note["body"]
    assert note["link"].startswith("/violations/")
    assert note["repository"]["full_name"] == "octo-org/project"
    assert [n["id"] for n in _inbox(victor)]  # viewers read violations
    assert _inbox(bob) == []  # another tenant
    assert alice.get("/api/v1/notifications/counts").data == {
        "unread": 1,
        "unread_critical": 0,
        "capped": False,
    }
    created = [
        e
        for e in ndash.store.list_audit_events(installation_id=42, limit=500)
        if e.type is AuditEventType.NOTIFICATION_CREATED
    ]
    assert [e.data["recipients"] for e in created] == [4]  # alice, victor, sam, ada

    # Rescanning the same commits changes nothing; a replayed webhook is a duplicate.
    make_ops(ndash).pull_request(
        hub.dev.git("rev-parse", "main"), hub.dev.git("rev-parse", "feature")
    )
    assert _run(ndash).dispatched == 0
    assert len(_inbox(alice)) == 1


def test_notification_idor_and_state_changes(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    alice, sam, bob = ndash.sign_in(ALICE), ndash.sign_in(SAM), ndash.sign_in(BOB)
    blocked()
    _run(ndash)
    [note] = _inbox(alice)
    [sams] = _inbox(sam)
    assert note["id"] != sams["id"]  # every member has their own inbox row

    # Another user's notification, and another tenant: not found, never modified.
    assert sam.get(f"/api/v1/notifications/{note['id']}").status == 404
    assert bob.get(f"/api/v1/notifications/{note['id']}").status == 404
    assert bob.post(f"/api/v1/notifications/{note['id']}/read").status == 404
    assert sam.post(f"/api/v1/notifications/{note['id']}/archive").status == 404
    assert alice.get(f"/api/v1/notifications/{note['id']}").data["state"] == "unread"

    read = alice.post(f"/api/v1/notifications/{note['id']}/read")
    assert (read.status, read.data["state"]) == (200, "read")
    assert _inbox(alice, state="unread") == []
    archived = alice.post(f"/api/v1/notifications/{note['id']}/archive").data
    assert archived["state"] == "archived"
    assert _inbox(alice) == []  # archived notifications leave the default list
    assert len(_inbox(alice, state="archived")) == 1
    assert sam.post("/api/v1/notifications/read-all", {}).data == {"updated": 1}
    assert _inbox(sam, state="unread") == []
    reads = [
        e
        for e in ndash.store.list_audit_events(installation_id=42, limit=500)
        if e.type is AuditEventType.NOTIFICATION_READ
    ]
    assert reads  # repository-scoped read markers are audited
    assert alice.get("/api/v1/notifications", state="bogus").status == 400
    assert (
        alice.get("/api/v1/notifications", category="'; DROP TABLE notifications; --").status == 400
    )


def test_repository_visibility_is_applied_when_reading(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    blocked()
    _run(ndash)
    # Victor keeps his CommitGuard role but GitHub no longer shows him the repository.
    victor = ndash.sign_in(VICTOR, {42: set()})
    assert _inbox(victor) == []
    assert victor.get("/api/v1/notifications/counts").data["unread"] == 0


def test_policy_change_notifies_audit_readers_not_viewers(ndash) -> None:  # type: ignore[no-untyped-def]
    alice, victor, sam = ndash.sign_in(ALICE), ndash.sign_in(VICTOR), ndash.sign_in(SAM)
    saved = alice.put(
        f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
    )
    assert saved.status == 200, saved.raw
    _run(ndash)
    [note] = _inbox(sam)
    assert (note["type"], note["link"]) == ("policy_changed", f"/policies/{ORG}")
    assert "v0 → v1" in note["title"]
    assert _inbox(victor) == []
    assert len(_inbox(alice)) == 1


# --------------------------------------------------------------------------- #
# Preferences and organization settings
# --------------------------------------------------------------------------- #
def test_personal_preferences_mute_only_non_mandatory_types(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    victor = ndash.sign_in(VICTOR)
    muted = victor.request(
        "PATCH",
        "/api/v1/notification-preferences",
        body={"organization_id": ORG, "in_app": {"high_violation": False}},
    )
    assert muted.status == 200, muted.raw
    high = next(t for t in muted.data["types"] if t["type"] == "high_violation")
    assert high["personal_in_app"] is False
    forbidden = victor.request(
        "PATCH",
        "/api/v1/notification-preferences",
        body={"organization_id": ORG, "in_app": {"critical_violation": False}},
    )
    assert (forbidden.status, forbidden.error["field"]) == (400, "in_app.critical_violation")
    other_tenant = victor.request(
        "PATCH",
        "/api/v1/notification-preferences",
        body={"organization_id": 2002, "in_app": {"high_violation": False}},
    )
    assert other_tenant.status == 404
    blocked()
    _run(ndash)
    assert _inbox(victor) == []
    assert len(_inbox(ndash.sign_in(ALICE))) == 1  # only Victor's own inbox changed


def test_mandatory_critical_notifications_reach_every_eligible_member(ndash) -> None:  # type: ignore[no-untyped-def]
    victor = ndash.sign_in(VICTOR)
    store = ndash.store
    with store.transaction() as db:
        db.execute(
            "INSERT INTO notification_user_preferences VALUES (?, ?, 'critical_violation', 0, 0)",
            (VICTOR[0], ORG),
        )  # even a stored "mute" (not possible through the API) is ignored
        emit(
            db,
            NotificationEvent(
                type=NotificationType.CRITICAL_VIOLATION,
                account_id=ORG,
                severity=Severity.CRITICAL,
                installation_id=42,
                repository_id=5001,
                resource_type="violation",
                resource_id="a" * 32,
                dedup_key="critical_violation:42:5001:pull_request:7:custom_rule",
                title="Blocked: custom_rule in octo-org/project",
                body="A critical finding <script>alert(1)</script> was blocked.",
            ),
            ndash.clock(),
        )
    _run(ndash)
    [note] = _inbox(victor)
    assert note["severity"] == "critical"
    assert (
        note["body"] == "A critical finding <script>alert(1)</script> was blocked."
    )  # text, not HTML
    assert victor.get("/api/v1/notifications/counts").data["unread_critical"] == 1
    assert [n["id"] for n in _inbox(victor, category="critical")] == [note["id"]]


def test_organization_settings_require_manage_permission_and_confirmation(ndash) -> None:  # type: ignore[no-untyped-def]
    ada, sam, bob = ndash.sign_in(ADA), ndash.sign_in(SAM), ndash.sign_in(BOB)
    view = sam.get(f"/api/v1/organizations/{ORG}/notification-settings").data
    assert view["can_manage"] is False
    assert view["email_recipients"] == []
    assert view["webhooks"] == []
    assert view["channels"] == {"in_app": True, "email": True, "webhook": True, "mode": "test"}
    assert _enable(sam, ORG, {"high_violation": {"email": True}}).status == 403
    assert bob.get(f"/api/v1/organizations/{ORG}/notification-settings").status == 404
    forged = bob.put(
        f"/api/v1/organizations/{ORG}/notification-settings",
        {"expected_version": 0, "types": {"high_violation": {"email": False}}, "confirm": True},
    )
    assert forged.status == 404

    enabled = _enable(
        ada, ORG, {"high_violation": {"email": True}}, email_recipients=["Security@Example.com"]
    )
    assert enabled.status == 200, enabled.raw
    assert enabled.data["version"] == 1
    assert enabled.data["email_recipients"] == ["security@example.com"]

    # Turning deliveries off needs explicit confirmation; stale versions conflict.
    off = {"policy_changed": {"email": False}}
    assert _enable(ada, ORG, off).error["code"] == "CONFIRMATION_REQUIRED"
    stale = ada.put(
        f"/api/v1/organizations/{ORG}/notification-settings",
        {"expected_version": 0, "types": {}, "confirm": True},
    )
    assert stale.error["code"] == "CONFLICT"
    assert _enable(ada, ORG, off, confirm=True).status == 200
    mandatory = _enable(ada, ORG, {"installation_disconnected": {"in_app": False}}, confirm=True)
    assert (mandatory.status, mandatory.error["field"]) == (
        400,
        "types.installation_disconnected.in_app",
    )
    bad_address = _enable(ada, ORG, {}, email_recipients=["x@example.com\r\nBcc: evil@example.com"])
    assert bad_address.status == 400
    changes = [
        e
        for e in ndash.store.query(
            "SELECT document FROM audit_events WHERE type = ?", ("notification_settings_changed",)
        )
    ]
    assert len(changes) == 2
    assert "security@example.com" not in json.dumps([c["document"] for c in changes])  # masked


def test_email_outage_never_changes_the_security_decision(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    ada = ndash.sign_in(ADA)
    assert (
        _enable(
            ada, ORG, {"high_violation": {"email": True}}, email_recipients=["sec@example.com"]
        ).status
        == 200
    )
    email = ndash.app.service.notifications.email
    email.failure = DeliveryError("smtp_unavailable")
    _, head = blocked()
    _run(ndash)

    assert ndash.github.runs_for(head)[-1]["conclusion"] == "failure"  # still BLOCKED
    assert len(_inbox(ada)) == 1  # the dashboard notification exists
    [delivery] = ada.get(f"/api/v1/organizations/{ORG}/notification-deliveries").data
    assert (delivery["status"], delivery["attempts"], delivery["failure_code"]) == (
        "pending",
        1,
        "smtp_unavailable",
    )
    assert delivery["next_retry_at"] is not None
    _run(ndash)
    assert (
        ada.get(f"/api/v1/organizations/{ORG}/notification-deliveries").data[0]["attempts"] == 1
    )  # backoff

    email.failure = None  # provider restored
    ndash.clock.advance(minutes=2)
    _run(ndash)
    [sent] = ada.get(f"/api/v1/organizations/{ORG}/notification-deliveries").data
    assert (sent["status"], sent["attempts"]) == ("sent", 2)
    [message] = email.sent
    assert message.email.to == "sec@example.com"
    assert "Blocked: ai_coauthor" in message.email.subject
    assert f"{ORIGIN}/violations/" in message.email.text
    assert "\r" not in message.email.subject
    types = [
        e.type
        for e in ndash.store.list_audit_events(installation_id=42, limit=500)
        + [
            __import__(
                "commitguard.audit.models", fromlist=["AuditEvent"]
            ).AuditEvent.model_validate_json(r["document"])
            for r in ndash.store.query(
                "SELECT document FROM audit_events WHERE installation_id IS NULL"
            )
        ]
    ]
    assert AuditEventType.NOTIFICATION_DELIVERY_FAILED in types
    assert AuditEventType.NOTIFICATION_DELIVERED in types
    assert ndash.app.service.metrics.value("notification_retries") == 1
    assert ndash.app.service.metrics.value("notifications_sent") == 1


def test_permanent_delivery_failure_is_bounded(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    ada = ndash.sign_in(ADA)
    _enable(ada, ORG, {"high_violation": {"email": True}}, email_recipients=["sec@example.com"])
    email = ndash.app.service.notifications.email
    email.failure = DeliveryError("smtp_timeout")
    blocked()
    for _ in range(8):
        _run(ndash)
        ndash.clock.advance(hours=3)
    ada = ndash.sign_in(ADA)
    [delivery] = ada.get(f"/api/v1/organizations/{ORG}/notification-deliveries").data
    assert (delivery["status"], delivery["attempts"]) == ("failed", 5)
    assert ndash.app.service.metrics.value("notifications_failed") == 1
    assert ndash.app.service.metrics.value("notification_retries") == 4


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #
def test_webhook_endpoints_are_signed_confirmed_and_tenant_scoped(
    ndash, hub, blocked, make_ops
) -> None:  # type: ignore[no-untyped-def]
    ada, sam, bob = ndash.sign_in(ADA), ndash.sign_in(SAM), ndash.sign_in(BOB)
    url = "https://hooks.example.com/commitguard?team=security"
    base = f"/api/v1/organizations/{ORG}/notification-webhooks"
    assert ada.post(base, {"url": url}).error["code"] == "CONFIRMATION_REQUIRED"
    assert sam.post(base, {"url": url, "confirm": True}).status == 403
    assert bob.post(base, {"url": url, "confirm": True}).status == 404
    for bad in (
        "ftp://hooks.example.com/x",
        "https://user:pw@hooks.example.com/",
        "javascript:alert(1)",
    ):
        assert ada.post(base, {"url": bad, "confirm": True}).status == 400, bad
    ndash.clock.advance(minutes=20)  # sign-in older than the re-authentication window
    assert (
        ada.post(base, {"url": url, "confirm": True}).error["code"] == "REAUTHENTICATION_REQUIRED"
    )
    ada = ndash.sign_in(ADA)
    created = ada.post(base, {"url": url, "confirm": True})
    assert created.status == 201, created.raw
    secret = created.data["signing_secret"]
    assert secret.startswith("whsec_")
    settings = ada.get(f"/api/v1/organizations/{ORG}/notification-settings").data
    assert [w["url"] for w in settings["webhooks"]] == [url]
    assert secret not in json.dumps(settings)
    assert secret not in json.dumps(
        [dict(r) for r in ndash.store.query("SELECT * FROM notification_webhooks")]
    )

    blocked()
    alice = ndash.sign_in(ALICE)
    alice.put(
        f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
    )
    _run(ndash)
    transport = ndash.app.service.notifications.webhook_transport
    [request] = transport.requests  # policy_changed has webhooks on by default; high_violation not
    assert request.headers["X-CommitGuard-Event"] == "policy_changed"
    assert verify_signature(
        Secret(secret),
        timestamp=request.headers["X-CommitGuard-Timestamp"],
        signature=request.headers["X-CommitGuard-Signature"],
        body=request.body,
        now=int(request.headers["X-CommitGuard-Timestamp"]),
    )
    tampered = request.body.replace(b"policy_changed", b"policy_rolled_back")
    assert not verify_signature(
        Secret(secret),
        timestamp=request.headers["X-CommitGuard-Timestamp"],
        signature=request.headers["X-CommitGuard-Signature"],
        body=tampered,
        now=int(request.headers["X-CommitGuard-Timestamp"]),
    )
    replayed_late = int(request.headers["X-CommitGuard-Timestamp"]) + 3600
    assert not verify_signature(
        Secret(secret),
        timestamp=request.headers["X-CommitGuard-Timestamp"],
        signature=request.headers["X-CommitGuard-Signature"],
        body=request.body,
        now=replayed_late,
    )
    payload = json.loads(request.body)
    assert payload["type"] == "policy_changed"
    assert payload["url"] == f"{ORIGIN}/policies/{ORG}"
    for secret_value in ("ghs_", "ghu_", "BEGIN", "webhook-secret", "client-secret"):
        assert secret_value not in request.body.decode()

    endpoint = settings["webhooks"][0]["id"]
    assert bob.delete(f"{base}/{endpoint}").status == 404
    assert ada.delete(f"{base}/{endpoint}").status == 200
    assert ada.get(f"/api/v1/organizations/{ORG}/notification-settings").data["webhooks"] == []


# --------------------------------------------------------------------------- #
# Installation disconnect and reconnect
# --------------------------------------------------------------------------- #
def test_installation_disconnect_is_detected_once_and_marks_enforcement_at_risk(
    ndash, payloads
) -> None:  # type: ignore[no-untyped-def]
    ada, sam = ndash.sign_in(ADA), ndash.sign_in(SAM)
    app = ndash.app
    app.github.installations[42].suspended = True
    suspend = payloads.installation("suspend", 42, (payloads.REPO,))
    assert app.deliver("installation", suspend).status == 200
    assert app.deliver("installation", suspend).status == 200  # repeated: no second alert
    _run(ndash)
    [note] = _inbox(ada)
    assert note["type"] == "installation_disconnected"
    assert note["severity"] == "critical"
    assert "AT RISK" in note["body"]
    assert "Affected repositories: 1" in note["body"]
    assert _inbox(sam) == []  # github:manage only
    [repository] = ada.get("/api/v1/repositories").data
    assert repository["protection"] == "at_risk"
    audit = [e.type for e in ndash.store.list_audit_events(installation_id=42, limit=100)]
    assert audit.count(AuditEventType.INSTALLATION_SUSPENDED) == 2

    app.github.installations[42].suspended = False
    ndash.clock.advance(minutes=5)
    app.deliver("installation", payloads.installation("unsuspend", 42, (payloads.REPO,)))
    _run(ndash)
    types = [n["type"] for n in _inbox(ada)]
    assert types == ["installation_reconnected", "installation_disconnected"]
    assert "RESTORED" in _inbox(ada)[0]["body"]


def test_uninstall_notification_stays_readable_and_reinstall_recovers(ndash, payloads) -> None:  # type: ignore[no-untyped-def]
    alice = ndash.sign_in(ALICE)
    app = ndash.app
    del app.github.installations[42]
    app.deliver("installation", payloads.installation("deleted", 42, (payloads.REPO,)))
    _run(ndash)
    [note] = _inbox(alice)
    assert note["type"] == "installation_disconnected"
    assert "uninstalled" in note["body"]
    # Reinstalling creates a new installation for the same account: a recovery event.
    app.github.add_installation(43, (payloads.REPO,))
    app.deliver("installation", payloads.installation("created", 43, (payloads.REPO,)))
    _run(ndash)
    # The existing session still sees the removed installation's alert; a new session
    # sees what GitHub reports now (the new installation) and its recovery event.
    assert [n["type"] for n in _inbox(alice)] == ["installation_disconnected"]
    fresh = ndash.sign_in(ALICE, {43: {5001}})
    assert [n["type"] for n in _inbox(fresh)] == ["installation_reconnected"]
    # A late "suspend" for the removed installation cannot revive it.
    app.deliver("installation", payloads.installation("suspend", 42, (payloads.REPO,)))
    assert ndash.store.get_installation(42).state.value == "deleted"


def test_notification_retention_purges_old_history(ndash, hub, blocked, make_ops) -> None:  # type: ignore[no-untyped-def]
    alice = ndash.sign_in(ALICE)
    blocked()
    _run(ndash)
    assert len(_inbox(alice)) == 1
    ndash.clock.advance(days=91)
    assert ndash.app.service.notifications.purge_expired() == 1
    assert _inbox(ndash.sign_in(ALICE)) == []
    assert ndash.store.query("SELECT COUNT(*) AS n FROM notifications")[0]["n"] == 0
