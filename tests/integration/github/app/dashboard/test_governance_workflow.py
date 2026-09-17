"""Policy approval workflow, separation of duties, emergency publication and exceptions."""

from datetime import timedelta

import pytest

from commitguard.exceptions.service import InfrastructureError

ORG = 1001
REPO = 5001
ALICE = (501, "alice")  # owner
VICTOR = (502, "victor")  # viewer
SAM = (503, "sam")  # security manager
ADA = (504, "ada")  # admin
BOB = (601, "bob")  # owner of globex
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


def _types(dash) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        r["type"]
        for r in dash.store.query(
            "SELECT type FROM notification_events WHERE account_id = ? ORDER BY created_at", (ORG,)
        )
    ]


def _audit(browser, event_type):  # type: ignore[no-untyped-def]
    return browser.get("/api/v1/audit", type=event_type, limit=20).data


def _require_approval(alice) -> None:  # type: ignore[no-untyped-def]
    saved = alice.put(
        f"/api/v1/organizations/{ORG}/settings",
        {"expected_version": 0, "settings": {"require_policy_approval": True}},
    )
    assert saved.status == 200, saved.raw
    assert saved.data["settings"]["require_policy_approval"] is True


def test_approval_workflow_enforces_separation_of_duties(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    ada = dash.sign_in(ADA)
    victor = dash.sign_in(VICTOR)
    _require_approval(alice)

    direct = ada.put(
        f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"bot_identity": "block"}}
    )
    assert direct.status == 409
    assert direct.error["code"] == "APPROVAL_REQUIRED"

    body = {"target_type": "organization", "floors": {"bot_identity": "block"}, "reason": "no bots"}
    assert victor.post(f"/api/v1/organizations/{ORG}/policy-drafts", body).status == 403
    draft = ada.post(f"/api/v1/organizations/{ORG}/policy-drafts", body)
    assert draft.status == 201, draft.raw
    draft_id = draft.data["id"]
    assert draft.data["state"] == "draft"
    assert draft.data["can_publish"] is False  # approval required first

    early = ada.post(f"/api/v1/policy-drafts/{draft_id}/publish", {})
    assert early.status == 409
    assert early.error["code"] == "APPROVAL_REQUIRED"

    submitted = ada.post(f"/api/v1/policy-drafts/{draft_id}/submit")
    assert submitted.status == 200, submitted.raw
    assert submitted.data["state"] == "pending_approval"
    assert "policy_approval_requested" in _types(dash)

    own = ada.post(f"/api/v1/policy-drafts/{draft_id}/approve", {})
    assert own.status == 403
    assert "Separation of duties" in own.error["message"]
    assert victor.post(f"/api/v1/policy-drafts/{draft_id}/approve", {}).status == 403
    bob = dash.sign_in(BOB)
    assert bob.post(f"/api/v1/policy-drafts/{draft_id}/approve", {}).status == 404
    assert bob.get(f"/api/v1/policy-drafts/{draft_id}").status == 404

    approved = alice.post(f"/api/v1/policy-drafts/{draft_id}/approve", {"reason": "reviewed"})
    assert approved.status == 200, approved.raw
    assert approved.data["state"] == "approved"

    # Editing an approved draft discards the approval: what was approved is not what changed.
    edited = ada.request(
        "PATCH",
        f"/api/v1/policy-drafts/{draft_id}",
        body={"expected_revision": 1, "floors": {"bot_identity": "block", "ai_trailer": "warn"}},
    )
    assert edited.status == 200, edited.raw
    assert edited.data["state"] == "draft"
    assert [a["status"] for a in edited.data["approvals"]] == ["cancelled"]
    stale_edit = ada.request(
        "PATCH", f"/api/v1/policy-drafts/{draft_id}", body={"expected_revision": 1, "title": "x"}
    )
    assert stale_edit.status == 409
    assert ada.post(f"/api/v1/policy-drafts/{draft_id}/publish", {}).status == 409

    assert ada.post(f"/api/v1/policy-drafts/{draft_id}/submit").status == 200
    assert alice.post(f"/api/v1/policy-drafts/{draft_id}/approve", {}).status == 200
    published = ada.post(f"/api/v1/policy-drafts/{draft_id}/publish", {})
    assert published.status == 200, published.raw
    assert published.data["state"] == "published"
    assert published.data["published_version"] == 1
    policy = ada.get(f"/api/v1/policies/{ORG}").data
    assert policy["version"] == 1
    [version] = ada.get(f"/api/v1/policies/{ORG}/versions").data
    assert version["draft_id"] == draft_id
    assert version["emergency"] is False
    [change] = _audit(alice, "organization_policy_changed")
    assert change["data"]["draft"] == draft_id
    assert len(_audit(alice, "policy_approved")) == 2
    assert _audit(alice, "policy_approval_requested")


def test_rejection_requires_a_reason_and_is_final(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    ada = dash.sign_in(ADA)
    draft_id = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {"target_type": "organization", "defaults": {"ai_coauthor": "allow"}},
    ).data["id"]
    assert ada.post(f"/api/v1/policy-drafts/{draft_id}/submit").status == 200
    assert alice.post(f"/api/v1/policy-drafts/{draft_id}/reject", {}).status == 400
    rejected = alice.post(f"/api/v1/policy-drafts/{draft_id}/reject", {"reason": "weakens AI rule"})
    assert rejected.status == 200, rejected.raw
    assert rejected.data["state"] == "rejected"
    assert ada.post(f"/api/v1/policy-drafts/{draft_id}/publish", {}).status == 409


def test_weakening_draft_needs_confirmation_and_rebases_on_conflict(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    assert (
        ada.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
        ).status
        == 200
    )
    draft = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {"target_type": "organization", "floors": {"ai_coauthor": "warn"}, "reason": "migration"},
    ).data
    assert draft["weakening"] is True
    assert draft["base_version"] == 1
    unconfirmed = ada.post(f"/api/v1/policy-drafts/{draft['id']}/publish", {})
    assert unconfirmed.status == 409
    assert unconfirmed.error["code"] == "CONFIRMATION_REQUIRED"
    # Someone else publishes first: the draft's base is outdated.
    assert (
        ada.put(
            f"/api/v1/policies/{ORG}",
            {"expected_version": 1, "floors": {"ai_coauthor": "block", "bot_identity": "warn"}},
        ).status
        == 200
    )
    view = ada.get(f"/api/v1/policy-drafts/{draft['id']}").data
    assert view["rebase_required"] is True
    conflict = ada.post(f"/api/v1/policy-drafts/{draft['id']}/publish", {"confirm_weakening": True})
    assert conflict.status == 409
    assert conflict.error["code"] == "CONFLICT"


def test_emergency_publication_is_owner_only_reasoned_and_loud(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    ada = dash.sign_in(ADA)
    _require_approval(alice)
    draft_id = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {"target_type": "organization", "floors": {"ai_identity": "block"}},
    ).data["id"]
    assert (
        ada.post(f"/api/v1/policy-drafts/{draft_id}/emergency-publish", {"reason": "x"}).status
        == 403
    )
    no_reason = alice.post(f"/api/v1/policy-drafts/{draft_id}/emergency-publish", {})
    assert no_reason.status == 400
    published = alice.post(
        f"/api/v1/policy-drafts/{draft_id}/emergency-publish",
        {"reason": "active incident: agent commits reaching main"},
    )
    assert published.status == 200, published.raw
    assert published.data["emergency"] is True
    [version] = alice.get(f"/api/v1/policies/{ORG}/versions").data
    assert version["emergency"] is True
    [event] = _audit(alice, "policy_emergency_published")
    assert event["data"]["reason"] == "active incident: agent commits reaching main"
    assert "policy_emergency_published" in _types(dash)


def test_settings_weakening_needs_confirmation_reason_and_recent_sign_in(dash, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    _require_approval(alice)
    path = f"/api/v1/organizations/{ORG}/settings"
    relax = {"expected_version": 1, "settings": {"require_policy_approval": False}}
    assert alice.put(path, relax).status == 409  # CONFIRMATION_REQUIRED
    assert alice.put(path, {**relax, "confirm": True}).status == 400  # reason
    clock.advance(minutes=20)
    stale = alice.put(path, {**relax, "confirm": True, "reason": "pilot finished"})
    assert stale.status == 401
    assert stale.error["code"] == "REAUTHENTICATION_REQUIRED"
    alice = dash.sign_in(ALICE)
    done = alice.put(path, {**relax, "confirm": True, "reason": "pilot finished"})
    assert done.status == 200, done.raw
    assert "organization_settings_changed" in _types(dash)
    assert alice.put(path, relax).status == 409  # version 1 is outdated now
    assert dash.sign_in(ADA).get(path).data["can_manage"] is True
    assert dash.sign_in(SAM).put(path, {**relax, "expected_version": 2}).status == 403
    assert dash.sign_in(BOB).get(path).status == 404
    invalid = alice.put(path, {"expected_version": 2, "settings": {"timezone": "Mars/Base"}})
    assert invalid.status == 400
    assert invalid.error["field"] == "settings.timezone"


@pytest.fixture
def ai_pull_request(hub):  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    return base, head


def test_exception_lifecycle_request_approve_activate_expire(
    dash, ops, clock, ai_pull_request
) -> None:  # type: ignore[no-untyped-def]
    base, head = ai_pull_request
    alice, sam, victor, ada = (dash.sign_in(u) for u in (ALICE, SAM, VICTOR, ADA))
    assert (
        alice.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
        ).status
        == 200
    )
    ops.pull_request(base, head)
    [scan] = alice.get("/api/v1/scans", limit=1).data
    assert scan["result"] == "blocked"

    expires = (clock() + timedelta(days=10)).isoformat()
    request = {
        "rule_id": "ai_coauthor",
        "scope_type": "repository",
        "scope_id": REPO,
        "action": "warn",
        "reason": "migration of legacy history",
        "expires_at": expires,
    }
    path = f"/api/v1/organizations/{ORG}/exceptions"
    assert victor.post(path, request).status == 403
    assert dash.sign_in(BOB).post(path, request).status == 404
    too_long = sam.post(
        path, {**request, "expires_at": (clock() + timedelta(days=400)).isoformat()}
    )
    assert too_long.status == 400
    assert (
        sam.post(path, {**request, "expires_at": None}).status == 400
    )  # never implicit permanence
    assert sam.post(path, {**request, "permanent": True, "expires_at": None}).status == 403
    other_tenant_repo = sam.post(path, {**request, "scope_id": 7001})
    assert other_tenant_repo.status == 404

    created = sam.post(path, request)
    assert created.status == 201, created.raw
    exception_id = created.data["id"]
    assert created.data["status"] == "requested"  # ai_coauthor is high severity
    assert created.data["requires_approval"] is True
    assert sam.post(path, request).status == 409  # one open exception per rule and scope
    assert "exception_requested" in _types(dash)

    assert sam.post(f"/api/v1/exceptions/{exception_id}/approve", {}).status == 403
    assert victor.post(f"/api/v1/exceptions/{exception_id}/approve", {}).status == 403
    approved = ada.post(f"/api/v1/exceptions/{exception_id}/approve", {"note": "until migrated"})
    assert approved.status == 200, approved.raw
    assert approved.data["status"] == "active"
    assert approved.data["decided_by"] == "ada"

    rescan = alice.post(f"/api/v1/scans/{scan['id']}/rescan")
    assert rescan.status == 202
    dash.app.run()
    detail = alice.get(f"/api/v1/scans/{rescan.data['scan']}").data
    assert detail["scan"]["result"] == "warning"
    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy")
    rule = next(r for r in effective.data["effective"]["rules"] if r["policy_id"] == "ai_coauthor")
    assert (rule["action"], rule["source"], rule["exception_id"]) == (
        "warn",
        "exception",
        exception_id,
    )
    assert effective.meta["exceptions"] == {"active": 1, "expiring_soon": 0}
    [row] = alice.get(f"/api/v1/organizations/{ORG}/security/repositories").data
    assert row["active_exceptions"] == 1
    assert row["posture"] == "at_risk"  # an exception lowers a high-severity rule

    governance = dash.app.service.governance
    clock.advance(days=8)
    assert governance.exceptions.warn_expiring() == 1
    assert governance.exceptions.warn_expiring() == 0  # never repeated per polling cycle
    assert _types(dash).count("exception_expiring") == 1

    clock.advance(days=3)
    assert governance.run_maintenance()["exceptions_expired"] == 1
    alice = dash.sign_in(ALICE)  # eleven days later: a fresh session
    expired = alice.get(f"/api/v1/exceptions/{exception_id}").data
    assert expired["status"] == "expired"
    assert "exception_ended" in _types(dash)
    [event] = _audit(alice, "exception_expired")
    assert event["actor"]["type"] == "system"

    again = alice.post(f"/api/v1/scans/{rescan.data['scan']}/rescan")
    assert again.status == 202, again.raw
    dash.app.run()
    assert alice.get(f"/api/v1/scans/{again.data['scan']}").data["scan"]["result"] == "blocked"
    # History is kept: the database refuses to delete the exception row.
    with pytest.raises(InfrastructureError), dash.store.transaction() as db:
        db.execute("DELETE FROM policy_exceptions WHERE exception_id = ?", (exception_id,))
    assert alice.get(f"/api/v1/exceptions/{exception_id}").status == 200


def test_exception_revocation_and_scope_rules(dash, clock) -> None:  # type: ignore[no-untyped-def]
    sam, ada = dash.sign_in(SAM), dash.sign_in(ADA)
    expires = (clock() + timedelta(days=5)).isoformat()
    path = f"/api/v1/organizations/{ORG}/exceptions"
    # A low-severity rule at repository scope does not need approval...
    low = sam.post(
        path,
        {
            "rule_id": "malformed_trailer",
            "scope_type": "repository",
            "scope_id": REPO,
            "action": "allow",
            "reason": "tooling emits odd trailers",
            "expires_at": expires,
        },
    )
    assert low.status == 201, low.raw
    assert low.data["status"] == "active"
    # ...but an organization-wide one always does.
    wide = sam.post(
        path,
        {
            "rule_id": "malformed_trailer",
            "scope_type": "organization",
            "action": "allow",
            "reason": "tooling emits odd trailers",
            "expires_at": expires,
        },
    )
    assert wide.status == 201, wide.raw
    assert wide.data["status"] == "requested"
    assert sam.post(f"/api/v1/exceptions/{low.data['id']}/revoke", {"reason": "x"}).status == 403
    assert ada.post(f"/api/v1/exceptions/{low.data['id']}/revoke", {}).status == 400
    revoked = ada.post(f"/api/v1/exceptions/{low.data['id']}/revoke", {"reason": "tooling fixed"})
    assert revoked.status == 200, revoked.raw
    assert revoked.data["status"] == "revoked"
    cancelled = sam.post(f"/api/v1/exceptions/{wide.data['id']}/cancel")
    assert cancelled.status == 200, cancelled.raw
    assert cancelled.data["status"] == "cancelled"
    rule_events = ada.get("/api/v1/audit", rule="malformed_trailer", limit=20).data
    assert {e["type"] for e in rule_events} >= {"exception_requested", "exception_revoked"}
    by_exception = ada.get("/api/v1/audit", exception=low.data["id"], limit=20).data
    assert [e["type"] for e in by_exception] == ["exception_revoked", "exception_requested"]
    assert ada.get("/api/v1/audit", exception="nope").status == 400
    assert ada.get("/api/v1/audit", rule="nope").status == 400
    listed = ada.get(path, status="revoked").data
    assert [e["id"] for e in listed] == [low.data["id"]]
    assert dash.sign_in(BOB).get(f"/api/v1/exceptions/{low.data['id']}").status == 404


def test_removed_member_loses_access_on_the_next_request(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    ada = dash.sign_in(ADA)
    assert ada.get(f"/api/v1/organizations/{ORG}/settings").status == 200
    removed = alice.delete(f"/api/v1/organizations/{ORG}/members/{ADA[0]}")
    assert removed.status == 200, removed.raw
    # The same session, no sign-out: access is re-read from the database.
    after = ada.get(f"/api/v1/organizations/{ORG}/settings")
    assert after.status in (401, 404)
    assert "data" not in (after.json or {})
    [event] = alice.get("/api/v1/audit", type="member_removed").data
    assert event["data"]["sessions_ended"] >= 1  # ada has no other membership
