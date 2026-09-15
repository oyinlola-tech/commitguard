"""Organization policy: floors enforced by real scans, versions, confirmation, concurrency."""

import pytest

ALICE = (501, "alice")  # owner
ADA = (504, "ada")  # admin
ORG = 1001
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
ALLOW_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"


def _policy(browser):  # type: ignore[no-untyped-def]
    return browser.get(f"/api/v1/policies/{ORG}").data


def _save(browser, version, floors, **extra):  # type: ignore[no-untyped-def]
    return browser.put(
        f"/api/v1/policies/{ORG}", {"expected_version": version, "floors": floors, **extra}
    )


@pytest.fixture
def permissive_repository(hub, ops):  # type: ignore[no-untyped-def]
    """The trusted branch allows ai_coauthor; a pull request adds an AI-attributed commit."""
    hub.dev.commit("chore: relax policy\n", files={".commitguard.yaml": ALLOW_CONFIG})
    hub.dev.push("main")
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    return base, head


def test_policy_versions_drive_scans_and_history_is_preserved(
    dash, ops, permissive_repository
) -> None:  # type: ignore[no-untyped-def]
    base, head = permissive_repository
    alice = dash.sign_in(ALICE)

    # v0 (no organization policy): the repository's own configuration allows the commit.
    ops.pull_request(base, head)
    [first] = alice.get("/api/v1/scans").data
    assert first["result"] == "pass"
    first_detail = alice.get(f"/api/v1/scans/{first['id']}").data
    assert first_detail["organization_policy_version"] is None
    assert {p["id"]: p["action"] for p in first_detail["effective_policies"]}["ai_coauthor"] == "allow"
    [finding] = first_detail["findings"]
    assert finding["action"] == "allow"  # recorded, not hidden
    assert alice.get("/api/v1/violations").data == []

    # v1: the organization requires BLOCK; the repository cannot weaken it.
    saved = _save(alice, 0, {"ai_coauthor": "block"}, reason="org-wide rule")
    assert saved.status == 200, saved.raw
    assert saved.data["version"] == 1
    rule = next(r for r in saved.data["rules"] if r["policy_id"] == "ai_coauthor")
    assert rule == {
        **rule,
        "organization_floor": "block",
        "minimum_action": "block",
        "repository_override": "stricter_only",
        "source": "organization_policy",
    }
    rescan = alice.post(f"/api/v1/scans/{first['id']}/rescan")
    assert rescan.status == 202, rescan.raw
    dash.app.run()
    second = alice.get(f"/api/v1/scans/{rescan.data['scan']}").data
    assert second["scan"]["result"] == "blocked"
    assert second["organization_policy_version"] == 1
    assert {p["id"]: p["action"] for p in second["effective_policies"]}["ai_coauthor"] == "block"
    assert "organization policy v1" in second["policy_source"]
    assert second["policy_version"] != first_detail["policy_version"]
    assert dash.github.runs_for(head)[-1]["conclusion"] == "failure"

    # v2: a new floor for another policy; v1 scans still report v1.
    assert _save(alice, 1, {"ai_coauthor": "block", "bot_identity": "block"}).status == 200
    historical = alice.get(f"/api/v1/scans/{second['scan']['id']}").data
    assert historical["organization_policy_version"] == 1
    assert alice.get(f"/api/v1/scans/{first['id']}").data["organization_policy_version"] is None
    versions = alice.get(f"/api/v1/policies/{ORG}/versions").data
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["floors"] == {"ai_coauthor": "block"}
    assert versions[1]["created_by"] == {"id": ALICE[0], "login": "alice"}
    assert alice.get(f"/api/v1/policies/{ORG}/versions/1").data["reason"] == "org-wide rule"
    assert alice.get(f"/api/v1/policies/{ORG}/versions/9").status == 404

    # Repository detail shows the policy that evaluated the latest scan.
    repository = alice.get("/api/v1/repositories/5001").data
    assert repository["organization_policy_version"] == 1
    assert repository["effective_policy_scan"] == second["scan"]["id"]


def test_weakening_needs_confirmation_reason_and_recent_sign_in(dash, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    assert _save(alice, 0, {"ai_coauthor": "block", "bot_identity": "warn"}).status == 200

    preview = alice.post(f"/api/v1/policies/{ORG}/preview", {"floors": {"bot_identity": "warn"}})
    assert preview.data["weakening"] is True
    assert preview.data["changes"] == [
        {"policy_id": "ai_coauthor", "old": "block", "new": None, "weakening": True}
    ]

    unconfirmed = _save(alice, 1, {"bot_identity": "warn"}, reason="migration")
    assert (unconfirmed.status, unconfirmed.error["code"]) == (409, "CONFIRMATION_REQUIRED")
    no_reason = _save(alice, 1, {"bot_identity": "warn"}, confirm_weakening=True)
    assert (no_reason.status, no_reason.error["field"]) == (400, "reason")

    clock.advance(minutes=16)
    stale = _save(alice, 1, {"bot_identity": "warn"}, confirm_weakening=True, reason="migration")
    assert (stale.status, stale.error["code"]) == (401, "REAUTHENTICATION_REQUIRED")
    # Tightening does not need a recent sign-in.
    assert _save(alice, 1, {"ai_coauthor": "block", "bot_identity": "block"}).status == 200

    alice = dash.sign_in(ALICE)
    weakened = _save(alice, 2, {"bot_identity": "warn"}, confirm_weakening=True, reason="migration")
    assert weakened.status == 200, weakened.raw
    assert weakened.meta["changes"] == [
        {"policy_id": "ai_coauthor", "old": "block", "new": None, "weakening": True},
        {"policy_id": "bot_identity", "old": "block", "new": "warn", "weakening": True},
    ]
    [event] = alice.get("/api/v1/audit", type="organization_policy_changed", limit=1).data
    assert event["data"]["weakening"] is True
    assert event["data"]["old_version"] == 2 and event["data"]["new_version"] == 3
    assert event["summary"].startswith("Changed organization policy v2 → v3")


def test_concurrent_edits_conflict_instead_of_overwriting(dash) -> None:  # type: ignore[no-untyped-def]
    alice, ada = dash.sign_in(ALICE), dash.sign_in(ADA)
    base = _policy(alice)["version"]
    assert _save(alice, base, {"ai_coauthor": "block"}).status == 200
    conflict = _save(ada, base, {"bot_identity": "block"})
    assert (conflict.status, conflict.error["code"]) == (409, "CONFLICT")
    assert "version 1" in conflict.error["message"]
    policy = _policy(ada)
    assert policy["version"] == 1
    assert {r["policy_id"]: r["organization_floor"] for r in policy["rules"]}["bot_identity"] is None


def test_policy_update_is_atomic(dash, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)

    def fail(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("disk full")

    monkeypatch.setattr(dash.store, "insert_audit_event", fail)
    result = _save(alice, 0, {"ai_coauthor": "block"})
    assert (result.status, result.error["code"]) == (500, "INTERNAL_ERROR")
    assert "disk full" not in result.raw.decode()
    monkeypatch.undo()
    assert _policy(alice)["version"] == 0  # no version without its audit event


@pytest.mark.parametrize(
    ("floors", "field"),
    [
        ({"ai_coauthor": "allow"}, "floors.ai_coauthor"),
        ({"unknown_rule": "block"}, "floors"),
        ({"ai_coauthor": ["block"]}, "floors.ai_coauthor"),
        ("block", "floors"),
    ],
)
def test_invalid_policy_documents_are_rejected(dash, floors, field) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    result = alice.put(f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": floors})
    assert (result.status, result.error["field"]) == (400, field)


def test_identical_policy_is_not_a_new_version(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    assert _save(alice, 0, {"ai_coauthor": "block"}).status == 200
    assert _save(alice, 1, {"ai_coauthor": "block"}).status == 400


def test_warn_block_and_allow_outcomes_are_reported_exactly(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "bots")
    bot = hub.dev.commit(
        "chore(deps): bump\n", author="dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>"
    )
    hub.dev.push("bots")
    ops.pull_request(base, bot, number=11)
    [scan] = alice.get("/api/v1/scans").data
    assert scan["result"] == "warning"  # PASSED_WITH_WARNINGS, not "pass" and not "blocked"
    assert dash.github.runs_for(bot)[-1]["conclusion"] == "success"
    [warning] = alice.get("/api/v1/violations", action="warn").data
    assert (warning["rule_id"], warning["action"], warning["severity"]) == ("bot_identity", "warn", "low")
    overview = alice.get("/api/v1/dashboard/overview").data["summary"]
    assert (overview["open_violations"], overview["open_warnings"]) == (0, 1)
