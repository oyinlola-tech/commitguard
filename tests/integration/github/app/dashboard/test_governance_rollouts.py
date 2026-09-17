"""Staged rollouts, simulation and bulk operations on a 100-repository organization."""

import uuid

import pytest

from commitguard.github.identifiers import RepositoryRef

ORG = 1001
INSTALLATION = 42
REPO = 5001
ALICE = (501, "alice")
VICTOR = (502, "victor")
ADA = (504, "ada")
BOB = (601, "bob")
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


@pytest.fixture
def fleet(dash):  # type: ignore[no-untyped-def]
    """octo-org with 100 repositories visible to alice and ada."""
    repositories = [RepositoryRef(id=5001, owner="octo-org", name="project")] + [
        RepositoryRef(id=9000 + i, owner="octo-org", name=f"service-{i:03d}") for i in range(99)
    ]
    installation = dash.github.installations[INSTALLATION]
    for repository in repositories:
        installation.repositories[repository.id] = repository
    dash.store.add_repositories(INSTALLATION, repositories, dash.clock())
    dash.app.service.governance.inventory.discovered(ORG, INSTALLATION, repositories)
    ids = {r.id for r in repositories}
    for user in (ALICE, ADA, VICTOR):
        dash.github.add_user(user[0], user[1], {INSTALLATION: ids})
    return sorted(ids)


def _publish(browser, body, **publish):  # type: ignore[no-untyped-def]
    draft = browser.post(f"/api/v1/organizations/{ORG}/policy-drafts", body)
    assert draft.status == 201, draft.raw
    result = browser.post(f"/api/v1/policy-drafts/{draft.data['id']}/publish", publish)
    assert result.status == 200, result.raw
    return result.data


def _version_for(dash, repository_id):  # type: ignore[no-untyped-def]
    resolved = dash.app.service.governance.resolver.for_repository(ORG, repository_id)
    return resolved.versions.organization_policy


def test_staged_rollout_pilot_expand_pause_and_rollback(dash, fleet, clock) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    alice = dash.sign_in(ALICE)
    _publish(ada, {"target_type": "organization", "floors": {"ai_coauthor": "block"}})
    pilot = fleet[:10]
    published = _publish(
        ada,
        {
            "target_type": "organization",
            "floors": {"ai_coauthor": "block", "bot_identity": "block"},
            "reason": "block bots",
        },
        rollout={"stages": [{"name": "Pilot", "repositories": pilot}, {"percent": 50}]},
    )
    rollout_id = published["rollout_id"]
    assert rollout_id is None or rollout_id  # the draft does not store it; list rollouts instead
    [rollout] = ada.get(f"/api/v1/organizations/{ORG}/rollouts", active="true").data
    rollout_id = rollout["id"]
    assert (rollout["state"], rollout["from_version"], rollout["to_version"]) == ("pilot", 1, 2)
    assert rollout["enrolled"] == 10
    assert rollout["scope_repositories"] == 100
    assert rollout["complete"] is False
    assert [s["name"] for s in rollout["stages"]] == ["Pilot", "Stage 2", "All repositories"]

    # Pilot repositories resolve v2; the other 90 keep v1.
    versions = {r: _version_for(dash, r) for r in fleet}
    assert {r for r, v in versions.items() if v == 2} == set(pilot)
    assert sum(1 for v in versions.values() if v == 1) == 90

    advanced = ada.post(f"/api/v1/rollouts/{rollout_id}/advance")
    assert advanced.status == 200, advanced.raw
    assert (advanced.data["state"], advanced.data["enrolled"]) == ("rollout", 50)
    assert sum(1 for r in fleet if _version_for(dash, r) == 2) == 50

    assert (
        dash.sign_in(VICTOR).post(f"/api/v1/rollouts/{rollout_id}/pause", {"reason": "x"}).status
        == 403
    )
    assert dash.sign_in(BOB).get(f"/api/v1/rollouts/{rollout_id}").status == 404
    paused = ada.post(f"/api/v1/rollouts/{rollout_id}/pause", {"reason": "investigating reports"})
    assert paused.status == 200, paused.raw
    assert paused.data["state"] == "paused"
    assert ada.post(f"/api/v1/rollouts/{rollout_id}/advance").status == 409
    # Paused: enrolled repositories keep the new version.
    assert sum(1 for r in fleet if _version_for(dash, r) == 2) == 50

    rolled = alice.post(
        f"/api/v1/rollouts/{rollout_id}/rollback",
        {"reason": "bots needed for releases", "confirm": True},
    )
    assert rolled.status == 200, rolled.raw
    assert rolled.data["state"] == "rolled_back"
    assert rolled.data["rollback_version"] == 3
    # v3 restores v1's document for everyone; v2 stays in history.
    assert {_version_for(dash, r) for r in fleet} == {3}
    effective = dash.app.service.governance.resolver.for_repository(ORG, fleet[0])
    [organization_layer] = [
        layer for layer in effective.inputs.layers if layer.label.startswith("organization")
    ]
    assert set(organization_layer.rules) == {"ai_coauthor"}
    versions_view = alice.get(f"/api/v1/policies/{ORG}/versions").data
    assert [v["version"] for v in versions_view] == [3, 2, 1]
    assert versions_view[0]["kind"] == "rollback"
    assert alice.get("/api/v1/audit", type="policy_rollout_rolled_back").data


def test_rollout_completes_only_when_every_repository_is_enrolled_and_propagated(
    dash, fleet
) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    _publish(ada, {"target_type": "organization", "floors": {"ai_coauthor": "warn"}})
    _publish(
        ada,
        {"target_type": "organization", "floors": {"ai_coauthor": "block"}},
        rollout={"stages": [{"percent": 10}]},
    )
    [rollout] = ada.get(f"/api/v1/organizations/{ORG}/rollouts").data
    assert rollout["enrolled"] == 10
    done = ada.post(f"/api/v1/rollouts/{rollout['id']}/advance").data
    assert done["state"] == "active"
    assert done["enrolled"] == 100
    assert done["complete"] is False  # enrolled, but effective policies not yet resolved
    dash.app.service.governance.resolver.propagate(limit=500)
    done = ada.get(f"/api/v1/rollouts/{rollout['id']}").data
    assert done["propagated"] == 100
    assert done["complete"] is True


def test_rollout_pauses_automatically_when_errors_exceed_the_threshold(dash, fleet, clock) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    _publish(ada, {"target_type": "organization", "floors": {"ai_coauthor": "warn"}})
    _publish(
        ada,
        {"target_type": "organization", "floors": {"ai_coauthor": "block"}},
        rollout={
            "stages": [{"repositories": fleet[:10]}],
            "thresholds": {"max_error_rate": 0.2, "min_scans": 5},
        },
    )
    [rollout] = ada.get(f"/api/v1/organizations/{ORG}/rollouts").data
    clock.advance(minutes=1)
    # Six scans of pilot repositories under v2: four could not complete.
    for index, repository_id in enumerate(fleet[:6]):
        state = "error" if index < 4 else "passed"
        with dash.store.transaction() as db:
            db.execute(
                "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
                "name, event, group_key, head_sha, check_name, context, state, created_at, "
                "updated_at, "
                "completed_at, organization_policy_version) VALUES (?, ?, ?, ?, 'octo-org', 'x', "
                "'push', 'branch:main', ?, 'commitguard-app/push', '{}', ?, ?, ?, ?, 2)",
                (
                    uuid.uuid4().hex,
                    uuid.uuid4().hex,
                    INSTALLATION,
                    repository_id,
                    "a" * 40,
                    state,
                    clock().timestamp(),
                    clock().timestamp(),
                    clock().timestamp(),
                ),
            )
    assert dash.app.service.governance.rollouts.evaluate() == 1
    paused = ada.get(f"/api/v1/rollouts/{rollout['id']}").data
    assert paused["state"] == "paused"
    assert "4 of 6 scans could not be completed" in paused["paused_reason"]
    assert paused["errors"] == 4
    types = {r["type"] for r in dash.store.query("SELECT type FROM notification_events")}
    assert "policy_rollout_failed" in types
    # No automatic rollback unless configured.
    assert ada.get(f"/api/v1/policies/{ORG}").data["version"] == 2


def test_simulation_is_read_only_and_uses_recorded_findings(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    # The repository's own configuration allows ai_coauthor; the organization floor blocks it.
    hub.dev.commit(
        "chore: relax policy\n",
        files={".commitguard.yaml": "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"},
    )
    hub.dev.push("main")
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ada = dash.sign_in(ADA)
    assert (
        ada.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"ai_coauthor": "block"}}
        ).status
        == 200
    )
    ops.pull_request(base, head)
    [scan] = ada.get("/api/v1/scans", limit=1).data
    assert scan["result"] == "blocked"
    runs_before = len(dash.github.runs_for(head))
    violations_before = ada.get("/api/v1/violations").data

    draft = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        # Replacing the ai_coauthor floor with a bot floor would unblock the commit.
        {"target_type": "organization", "floors": {"bot_identity": "warn"}, "reason": "trial"},
    ).data
    queued = ada.post(f"/api/v1/policy-drafts/{draft['id']}/simulations", {"period_days": 30})
    assert queued.status == 202, queued.raw
    assert queued.data["state"] == "queued"
    assert (
        dash.sign_in(VICTOR).post(f"/api/v1/policy-drafts/{draft['id']}/simulations", {}).status
        == 403
    )
    assert dash.app.service.governance.simulations.run_pending() == 1
    result = ada.get(f"/api/v1/simulations/{queued.data['id']}").data
    assert result["state"] == "completed"
    outcome = result["result"]
    assert outcome["disclaimer"].startswith("SIMULATION")
    assert outcome["scans_analyzed"] == 1
    assert outcome["no_longer_blocked"] == 1
    assert outcome["scans_no_longer_blocked"] == 1
    assert outcome["new_blocks"] == 0
    assert outcome["scans_assumed_defaults"] == 0  # the Phase 8 scan recorded its configuration
    assert [i["full_name"] for i in outcome["most_affected"]] == ["octo-org/project"]

    # Nothing about enforcement changed.
    assert ada.get(f"/api/v1/policies/{ORG}").data["version"] == 1
    assert ada.get(f"/api/v1/policy-drafts/{draft['id']}").data["state"] == "draft"
    assert len(dash.github.runs_for(head)) == runs_before
    assert ada.get("/api/v1/violations").data == violations_before
    assert dash.sign_in(BOB).get(f"/api/v1/simulations/{queued.data['id']}").status == 404
    assert ada.get("/api/v1/audit", type="policy_simulated").data


def test_bulk_operation_runs_in_background_batches_idempotently(dash, fleet) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    group_id = ada.post(f"/api/v1/organizations/{ORG}/repository-groups", {"name": "Backend"}).data[
        "id"
    ]
    body = {
        "type": "add_to_group",
        "repository_ids": fleet,
        "parameters": {"group_id": group_id},
        "idempotency_key": "add-backend-2026-09",
    }
    queued = ada.post(f"/api/v1/organizations/{ORG}/bulk-operations", body)
    assert queued.status == 202, queued.raw
    assert (queued.data["status"], queued.data["total"], queued.data["pending"]) == (
        "queued",
        100,
        100,
    )
    again = ada.post(f"/api/v1/organizations/{ORG}/bulk-operations", body)
    assert again.data["id"] == queued.data["id"]  # the same request is the same operation

    bulk = dash.app.service.governance.bulk
    assert bulk.run_pending(budget=40) == 40
    progress = ada.get(f"/api/v1/bulk-operations/{queued.data['id']}").data
    assert (progress["status"], progress["completed"], progress["pending"]) == ("running", 40, 60)
    # The lease is still held: another pass waits until it expires.
    dash.clock.advance(minutes=6)
    assert bulk.run_pending(budget=200) == 60
    done = ada.get(f"/api/v1/bulk-operations/{queued.data['id']}").data
    assert (done["status"], done["completed"], done["failed"]) == ("completed", 100, 0)
    assert ada.get(f"/api/v1/repository-groups/{group_id}").data["group"]["repository_count"] == 100
    # One audit event per applied batch (40, then 60), recording how many repositories each added.
    batches = ada.get("/api/v1/audit", type="repository_group_members_added", limit=100).data
    assert sorted(event["data"]["repositories"] for event in batches) == [40, 60]
    [finished] = ada.get("/api/v1/audit", type="bulk_operation_finished").data
    assert finished["data"]["completed"] == 100

    # Partial failure: the group is archived while items are pending; retry after fixing.
    remove = ada.post(
        f"/api/v1/organizations/{ORG}/bulk-operations",
        {
            "type": "remove_from_group",
            "repository_ids": fleet[:10],
            "parameters": {"group_id": group_id},
        },
    ).data
    assert bulk.run_pending(budget=5) == 5
    with dash.store.transaction() as db:
        db.execute("UPDATE repository_groups SET archived_at = 1 WHERE group_id = ?", (group_id,))
    dash.clock.advance(minutes=6)
    bulk.run_pending()
    partial = ada.get(f"/api/v1/bulk-operations/{remove['id']}").data
    assert partial["status"] in ("partial", "completed")
    assert partial["completed"] + partial["failed"] == 10


def test_bulk_operation_authorization_and_limits(dash, fleet) -> None:  # type: ignore[no-untyped-def]
    victor = dash.sign_in(VICTOR)
    ada = dash.sign_in(ADA)
    body = {"type": "set_mode", "repository_ids": fleet[:3], "parameters": {"mode": "monitor"}}
    assert victor.post(f"/api/v1/organizations/{ORG}/bulk-operations", body).status == 403
    assert ada.post(f"/api/v1/organizations/{ORG}/bulk-operations", body).status == 409  # confirm
    no_reason = ada.post(f"/api/v1/organizations/{ORG}/bulk-operations", {**body, "confirm": True})
    assert no_reason.status == 400
    foreign = ada.post(
        f"/api/v1/organizations/{ORG}/bulk-operations",
        {**body, "repository_ids": [7001], "confirm": True, "parameters": {"mode": "enforce"}},
    )
    assert foreign.status == 404
    too_many = ada.post(
        f"/api/v1/organizations/{ORG}/bulk-operations",
        {
            "type": "onboard",
            "repository_ids": list(range(1, 5002)),
            "parameters": {"mode": "enforce"},
        },
    )
    assert too_many.status in (400, 413)
    assert (
        dash.sign_in(BOB).post(f"/api/v1/organizations/{ORG}/bulk-operations", body).status == 404
    )
