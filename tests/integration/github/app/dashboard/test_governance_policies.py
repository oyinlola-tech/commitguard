"""Organization governance through real scans: inheritance, mandatory floors, conflicts, mode."""

import pytest

ORG = 1001
REPO = 5001
ALICE = (501, "alice")  # owner
VICTOR = (502, "victor")  # viewer
SAM = (503, "sam")  # security manager
ADA = (504, "ada")  # admin
BOB = (601, "bob")  # owner of the other tenant (globex)
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
ALLOW_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"


@pytest.fixture
def ai_pull_request(hub):  # type: ignore[no-untyped-def]
    """A pull request adding an AI-attributed commit; the trusted base allows ai_coauthor."""
    hub.dev.commit("chore: relax policy\n", files={".commitguard.yaml": ALLOW_CONFIG})
    hub.dev.push("main")
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    return base, head


def _latest_scan(browser):  # type: ignore[no-untyped-def]
    [scan] = browser.get("/api/v1/scans", limit=1).data
    return browser.get(f"/api/v1/scans/{scan['id']}").data


def _publish_draft(browser, body, **publish):  # type: ignore[no-untyped-def]
    created = browser.post(f"/api/v1/organizations/{ORG}/policy-drafts", body)
    assert created.status == 201, created.raw
    published = browser.post(f"/api/v1/policy-drafts/{created.data['id']}/publish", publish)
    assert published.status == 200, published.raw
    return published.data


def _rule(view, policy_id):  # type: ignore[no-untyped-def]
    return next(r for r in view["effective"]["rules"] if r["policy_id"] == policy_id)


def test_mandatory_organization_policy_overrides_repository_and_explains_the_conflict(
    dash, ops, ai_pull_request
) -> None:  # type: ignore[no-untyped-def]
    base, head = ai_pull_request
    alice = dash.sign_in(ALICE)
    saved = alice.put(
        f"/api/v1/policies/{ORG}",
        {"expected_version": 0, "floors": {"ai_coauthor": "block"}, "reason": "baseline"},
    )
    assert saved.status == 200, saved.raw

    ops.pull_request(base, head)
    scan = _latest_scan(alice)
    assert scan["scan"]["result"] == "blocked"
    assert dash.github.runs_for(head)[-1]["conclusion"] == "failure"

    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy")
    assert effective.status == 200, effective.raw
    rule = _rule(effective.data, "ai_coauthor")
    assert rule["action"] == "block"
    assert rule["source"] == "organization"
    assert rule["enforcement"] == "mandatory"
    # The repository's own configuration is only known at scan time: the scan recorded it.
    last = effective.data["last_scan_effective"]
    [conflict] = [r["conflict"] for r in last["rules"] if r["conflict"]]
    assert conflict["requested_action"] == "allow"
    assert conflict["requested_by"] == "repository_configuration"
    assert conflict["required_action"] == "block"
    assert "cannot weaken" in conflict["reason"]
    assert effective.data["last_scan_used_current_policy"] is True

    matrix = alice.get(f"/api/v1/organizations/{ORG}/security/repositories", drift="drift")
    assert matrix.status == 200, matrix.raw
    [row] = matrix.data
    assert row["repository_id"] == REPO
    [difference] = row["drift_differences"]
    assert difference == {
        "policy_id": "ai_coauthor",
        "requested": "allow",
        "requested_by": "Repository configuration (.commitguard.yaml)",
        "required": "block",
        "required_by": "organization policy v1",
        "effective": "block",
    }


def test_group_and_repository_policies_resolve_with_precedence(dash, ops, ai_pull_request) -> None:  # type: ignore[no-untyped-def]
    base, head = ai_pull_request
    ada = dash.sign_in(ADA)
    group = ada.post(
        f"/api/v1/organizations/{ORG}/repository-groups", {"name": "Backend", "description": "APIs"}
    )
    assert group.status == 201, group.raw
    group_id = group.data["id"]
    members = ada.post(
        f"/api/v1/repository-groups/{group_id}/repositories", {"repository_ids": [REPO]}
    )
    assert members.status == 200, members.raw
    assert [r["repository_id"] for r in members.data["repositories"]] == [REPO]

    # Group: bot_identity defaults to block; ai_coauthor is mandatory block for the group.
    _publish_draft(
        ada,
        {
            "target_type": "group",
            "target_id": group_id,
            "floors": {"ai_coauthor": "block"},
            "defaults": {"bot_identity": "block"},
            "reason": "production controls",
        },
    )
    # Repository policy tries to allow ai_coauthor (cannot) and relaxes bot_identity (can).
    _publish_draft(
        ada,
        {
            "target_type": "repository",
            "target_id": REPO,
            "defaults": {"ai_coauthor": "allow", "bot_identity": "warn"},
            "reason": "legacy repository",
        },
        confirm_weakening=True,
    )
    effective = ada.get(f"/api/v1/repositories/{REPO}/effective-policy").data
    assert effective["versions"]["groups"] == {group_id: 1}
    assert effective["versions"]["repository_policy"] == 1
    ai = _rule(effective, "ai_coauthor")
    assert (ai["action"], ai["source"], ai["required_label"]) == (
        "block",
        "group",
        "group Backend policy v1",
    )
    assert ai["conflict"]["requested_by"] == "repository_policy"
    bot = _rule(effective, "bot_identity")
    assert (bot["action"], bot["source"]) == ("warn", "repository_policy")

    ops.pull_request(base, head)
    scan = _latest_scan(ada)
    assert scan["scan"]["result"] == "blocked"
    assert "group Backend policy v1" in scan["policy_source"]

    targets = ada.get(f"/api/v1/organizations/{ORG}/policies").data
    assert [g["target"]["label"] for g in targets["groups"]] == ["Group Backend"]
    assert [r["target"]["id"] for r in targets["repositories"]] == [str(REPO)]
    versions = ada.get(
        f"/api/v1/organizations/{ORG}/policy-targets/group/versions", target_id=group_id
    )
    assert versions.status == 200, versions.raw
    assert [v["version"] for v in versions.data["versions"]] == [1]

    # The other tenant sees none of it.
    bob = dash.sign_in(BOB)
    assert bob.get(f"/api/v1/repository-groups/{group_id}").status == 404
    assert bob.get(f"/api/v1/repositories/{REPO}/effective-policy").status == 404
    assert (
        bob.get(
            f"/api/v1/organizations/{ORG}/policy-targets/group/versions", target_id=group_id
        ).status
        == 404
    )
    assert (
        bob.post(
            f"/api/v1/repository-groups/{group_id}/repositories", {"repository_ids": [7001]}
        ).status
        == 404
    )


def test_group_membership_is_tenant_scoped(dash) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    group_id = ada.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": "Mobile"}).data[
        "id"
    ]
    # 7001 belongs to globex: adding it to an octo-org group is "not found", not a leak.
    other = ada.post(
        f"/api/v1/repository-groups/{group_id}/repositories", {"repository_ids": [7001]}
    )
    assert other.status == 404
    duplicate = ada.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": " mobile "})
    assert duplicate.status == 409
    victor = dash.sign_in(VICTOR)
    assert (
        victor.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": "X"}).status == 403
    )
    assert victor.get(f"/api/v1/repository-groups/{group_id}").status == 200
    bob = dash.sign_in(BOB)
    assert bob.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": "X"}).status == 404


def test_monitor_mode_reports_without_blocking_and_still_alerts(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ada = dash.sign_in(ADA)
    body = {"repository_ids": [REPO], "mode": "monitor"}
    unconfirmed = ada.post(f"/api/v1/organizations/{ORG}/repositories/mode", body)
    assert unconfirmed.status == 409
    assert unconfirmed.error["code"] == "CONFIRMATION_REQUIRED"
    assert "stops CommitGuard from blocking" in unconfirmed.error["message"]
    no_reason = ada.post(
        f"/api/v1/organizations/{ORG}/repositories/mode", {**body, "confirm": True}
    )
    assert no_reason.status == 400
    changed = ada.post(
        f"/api/v1/organizations/{ORG}/repositories/mode",
        {**body, "confirm": True, "reason": "observe before enforcing"},
    )
    assert changed.status == 200, changed.raw
    assert changed.data["changed"] == [REPO]

    ops.pull_request(base, head)
    scan = _latest_scan(ada)
    assert scan["scan"]["result"] == "warning"
    assert dash.github.runs_for(head)[-1]["conclusion"] != "failure"
    rule = next(r for r in scan["effective_policies"] if r["id"] == "ai_coauthor")
    assert rule["action"] == "warn"
    titles = [
        r["title"]
        for r in dash.store.query(
            "SELECT title FROM notification_events WHERE account_id = ?", (ORG,)
        )
    ]
    assert any(t.startswith("Would block (monitor mode): ai_coauthor") for t in titles)

    [row] = ada.get(f"/api/v1/organizations/{ORG}/security/repositories").data
    assert row["mode"] == "monitor"
    assert row["posture"] == "at_risk"
    assert any("Monitor mode" in reason for reason in row["posture_reasons"])

    [event] = ada.get("/api/v1/audit", type="repository_mode_changed", limit=1).data
    assert event["actor"]["login"] == "ada"

    back = ada.post(
        f"/api/v1/organizations/{ORG}/repositories/mode",
        {"repository_ids": [REPO], "mode": "enforce", "confirm": True},
    )
    assert back.status == 200, back.raw
    rescan = ada.post(f"/api/v1/scans/{scan['scan']['id']}/rescan")
    assert rescan.status == 202, rescan.raw
    dash.app.run()
    assert _latest_scan(ada)["scan"]["result"] == "blocked"
    # A viewer cannot change modes.
    victor = dash.sign_in(VICTOR)
    assert (
        victor.post(
            f"/api/v1/organizations/{ORG}/repositories/mode",
            {**body, "confirm": True, "reason": "x"},
        ).status
        == 403
    )


def test_policy_changes_invalidate_and_propagate_effective_policies(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    governance = dash.app.service.governance
    # Resolve once: up to date.
    governance.resolver.propagate()
    status = alice.get(f"/api/v1/organizations/{ORG}/policy-propagation").data
    assert status["complete"] is True
    assert status["up_to_date"] == status["repositories"] == 1

    assert (
        alice.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"bot_identity": "block"}}
        ).status
        == 200
    )
    status = alice.get(f"/api/v1/organizations/{ORG}/policy-propagation").data
    assert status["complete"] is False  # never reported as propagated before it is
    assert status["stale"] == 1

    governance.resolver.propagate()
    status = alice.get(f"/api/v1/organizations/{ORG}/policy-propagation").data
    assert status["complete"] is True
    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy").data
    assert effective["versions"]["organization_policy"] == 1
    assert _rule(effective, "bot_identity")["action"] == "block"
    # The other tenant's cache is untouched by octo-org's change.
    rows = dash.store.query(
        "SELECT account_id, state FROM repository_effective_policies ORDER BY account_id"
    )
    assert {(r["account_id"], r["state"]) for r in rows} == {
        (1001, "up_to_date"),
        (2002, "up_to_date"),
    }
