"""The Phase 8 end-to-end scenario, through GitHub webhooks, real Git scans and the API.

1  organization          2  GitHub App connected       3  repositories discovered
4  ten selected          5  group created              6  repositories assigned (bulk)
7  security baseline     8  organization policy draft  9-10  simulation reviewed
11 approved (by another administrator)  12 published  13 propagated  14 effective policy
15 repository override  16 mandatory policy holds     17-19 exception approved and in effect
20-22 scan, violation, notification                    23 organization dashboard
24-28 staged rollout: pilot, expand, failure, pause     29 rollback
30 historical scans unchanged   31 new scans use the rolled-back policy
32-34 installation disconnected: at risk, administrators notified
35-37 reconnected, synchronised, enforcement recovered  38 report   39 audit trail
"""

import json
import uuid
from datetime import timedelta

import pytest

from commitguard.github.identifiers import RepositoryRef

ORG = 1001
INSTALLATION = 42
REPO = 5001
ALICE = (501, "alice")  # owner
SAM = (503, "sam")  # security manager
ADA = (504, "ada")  # admin
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
PROJECT = RepositoryRef(id=REPO, owner="octo-org", name="project")


@pytest.fixture(autouse=True)
def _no_rate_limits(monkeypatch):  # type: ignore[no-untyped-def]
    from commitguard.security.rate_limit import RequestRateLimiter

    monkeypatch.setattr(RequestRateLimiter, "allow", lambda self, key: True)


def test_phase_8_end_to_end(dash, ops, hub, payloads, clock) -> None:  # type: ignore[no-untyped-def]
    governance = dash.app.service.governance

    def sign_in_all():  # type: ignore[no-untyped-def]
        return dash.sign_in(ALICE), dash.sign_in(ADA), dash.sign_in(SAM)

    # 1-3. The organization's installation is active; GitHub adds ten more repositories.
    services = [RepositoryRef(id=9100 + i, owner="octo-org", name=f"svc-{i}") for i in range(10)]
    installation = dash.github.installations[INSTALLATION]
    for repository in services:
        installation.repositories[repository.id] = repository
    added = dash.app.deliver(
        "installation_repositories", payloads.repositories("added", tuple(services), INSTALLATION)
    )
    assert added.status == 200
    all_ids = {REPO, *(r.id for r in services)}
    for user in (ALICE, ADA, SAM):
        dash.github.add_user(user[0], user[1], {INSTALLATION: all_ids})
    alice, ada, sam = sign_in_all()
    discovered = alice.get("/api/v1/audit", type="repository_discovered").data
    assert [event["data"]["repositories"] for event in discovered] == [10, 1]  # newest first

    # 4-6. Ten repositories (the project and nine services) go into "Production".
    selected = [REPO, *(r.id for r in services[:9])]
    group_id = ada.post(
        f"/api/v1/organizations/{ORG}/repository-groups", {"name": "Production"}
    ).data["id"]
    bulk = ada.post(
        f"/api/v1/organizations/{ORG}/bulk-operations",
        {"type": "add_to_group", "repository_ids": selected, "parameters": {"group_id": group_id}},
    ).data
    governance.run_maintenance()
    assert ada.get(f"/api/v1/bulk-operations/{bulk['id']}").data["status"] == "completed"
    assert ada.get(f"/api/v1/repository-groups/{group_id}").data["group"]["repository_count"] == 10

    # 7. Security baseline, and approval for policy changes.
    settings = alice.put(
        f"/api/v1/organizations/{ORG}/settings",
        {
            "expected_version": 0,
            "settings": {
                "security_baseline": {"ai_identity": "block"},
                "require_policy_approval": True,
            },
        },
    )
    assert settings.status == 200, settings.raw

    # A historical scan to simulate against: an AI-attributed pull request, blocked by default.
    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(AI)
    hub.dev.push("feature")
    ops.pull_request(base, head)
    [first_scan] = alice.get("/api/v1/scans", limit=1).data
    assert first_scan["result"] == "blocked"
    first_detail = alice.get(f"/api/v1/scans/{first_scan['id']}").data

    # 8-12. Organization policy: draft, simulate, review, approve, publish.
    draft = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {
            "target_type": "organization",
            "title": "AI attribution floor",
            "floors": {"ai_coauthor": "block"},
            "reason": "no AI co-authors on any repository",
        },
    ).data
    simulation = ada.post(f"/api/v1/policy-drafts/{draft['id']}/simulations", {}).data
    governance.run_maintenance()
    reviewed = ada.get(f"/api/v1/simulations/{simulation['id']}").data
    assert reviewed["state"] == "completed"
    assert reviewed["result"]["scans_analyzed"] == 1
    assert reviewed["result"]["disclaimer"].startswith("SIMULATION")
    assert ada.post(f"/api/v1/policy-drafts/{draft['id']}/submit").status == 200
    assert ada.post(f"/api/v1/policy-drafts/{draft['id']}/approve", {}).status == 403
    assert alice.post(f"/api/v1/policy-drafts/{draft['id']}/approve", {}).status == 200
    published = ada.post(f"/api/v1/policy-drafts/{draft['id']}/publish", {})
    assert published.status == 200, published.raw

    # 13-14. Propagation, then the effective policy with provenance.
    status = alice.get(f"/api/v1/organizations/{ORG}/policy-propagation").data
    assert status["complete"] is False
    governance.run_maintenance()
    status = alice.get(f"/api/v1/organizations/{ORG}/policy-propagation").data
    assert (status["complete"], status["repositories"]) == (True, 11)
    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy").data
    rules = {r["policy_id"]: r for r in effective["effective"]["rules"]}
    assert (rules["ai_coauthor"]["action"], rules["ai_coauthor"]["source"]) == (
        "block",
        "organization",
    )
    assert rules["ai_identity"]["required_label"] == "security baseline (settings v1)"

    # 15-16. A repository override cannot weaken the mandatory organization policy.
    override = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {
            "target_type": "repository",
            "target_id": REPO,
            "defaults": {"ai_coauthor": "allow"},
            "reason": "legacy repository asks to allow AI co-authors",
        },
    ).data
    assert ada.post(f"/api/v1/policy-drafts/{override['id']}/submit").status == 200
    assert alice.post(f"/api/v1/policy-drafts/{override['id']}/approve", {}).status == 200
    assert (
        ada.post(
            f"/api/v1/policy-drafts/{override['id']}/publish", {"confirm_weakening": True}
        ).status
        == 200
    )
    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy").data
    ai = next(r for r in effective["effective"]["rules"] if r["policy_id"] == "ai_coauthor")
    assert ai["action"] == "block"
    assert ai["conflict"]["requested_by"] == "repository_policy"

    # 17-19. A temporary exception, approved by someone else, lowers it to warn.
    exception = sam.post(
        f"/api/v1/organizations/{ORG}/exceptions",
        {
            "rule_id": "ai_coauthor",
            "scope_type": "repository",
            "scope_id": REPO,
            "action": "warn",
            "reason": "migration of legacy history",
            "expires_at": (clock() + timedelta(days=14)).isoformat(),
        },
    ).data
    assert ada.post(f"/api/v1/exceptions/{exception['id']}/approve", {}).status == 200
    effective = alice.get(f"/api/v1/repositories/{REPO}/effective-policy").data
    ai = next(r for r in effective["effective"]["rules"] if r["policy_id"] == "ai_coauthor")
    assert (ai["action"], ai["exception_id"]) == ("warn", exception["id"])

    # 20-22. Scans: warning under the exception; revoked -> blocked, violation, notification.
    rescan = alice.post(f"/api/v1/scans/{first_scan['id']}/rescan").data
    dash.app.run()
    assert alice.get(f"/api/v1/scans/{rescan['scan']}").data["scan"]["result"] == "warning"
    assert (
        ada.post(
            f"/api/v1/exceptions/{exception['id']}/revoke", {"reason": "history migrated"}
        ).status
        == 200
    )
    rescan = alice.post(f"/api/v1/scans/{rescan['scan']}/rescan").data
    dash.app.run()
    blocked = alice.get(f"/api/v1/scans/{rescan['scan']}").data
    assert blocked["scan"]["result"] == "blocked"
    assert [v["rule_id"] for v in alice.get("/api/v1/violations", status="open").data] == [
        "ai_coauthor"
    ]
    dash.app.service.notifications.run_once()
    inbox = {n["type"] for n in alice.get("/api/v1/notifications", limit=100).data}
    assert {"high_violation", "exception_approved", "exception_ended", "policy_changed"} <= inbox

    # 23. Organization dashboard.
    overview = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    assert overview["repositories"] == 11
    assert overview["policy"]["organization_version"] == 1
    assert "11 required repositories" in overview["compliance"] or "of 11" in overview["compliance"]

    # 24-26. Staged rollout of a stricter organization policy: pilot, then expand.
    stricter = ada.post(
        f"/api/v1/organizations/{ORG}/policy-drafts",
        {
            "target_type": "organization",
            "floors": {"ai_coauthor": "block", "bot_identity": "block"},
        },
    ).data
    assert ada.post(f"/api/v1/policy-drafts/{stricter['id']}/submit").status == 200
    assert alice.post(f"/api/v1/policy-drafts/{stricter['id']}/approve", {}).status == 200
    rolled_out = ada.post(
        f"/api/v1/policy-drafts/{stricter['id']}/publish",
        {
            "rollout": {
                "stages": [{"name": "Pilot", "repositories": selected[:3]}, {"percent": 50}],
                "thresholds": {"max_error_rate": 0.25, "min_scans": 4},
            }
        },
    )
    assert rolled_out.status == 200, rolled_out.raw
    [rollout] = ada.get(f"/api/v1/organizations/{ORG}/rollouts", active="true").data
    assert rollout["enrolled"] == 3
    pilot_versions = {
        r: governance.resolver.for_repository(ORG, r).versions.organization_policy for r in all_ids
    }
    assert sorted(r for r, v in pilot_versions.items() if v == 2) == sorted(selected[:3])
    expanded = ada.post(f"/api/v1/rollouts/{rollout['id']}/advance").data
    assert expanded["enrolled"] == 6  # 50 % of 11, rounded up

    # 27-28. A policy issue: enrolled repositories' scans fail; the rollout pauses itself.
    clock.advance(minutes=1)
    enrolled = [
        int(r["repository_id"])
        for r in dash.store.query(
            "SELECT repository_id FROM policy_rollout_repositories WHERE rollout_id = ?",
            (rollout["id"],),
        )
    ]
    for repository_id in enrolled[:5]:
        with dash.store.transaction() as db:
            db.execute(
                "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
                "name, event, group_key, head_sha, check_name, context, state, created_at, "
                "updated_at, completed_at, organization_policy_version, failure_kind) VALUES "
                "(?, ?, ?, ?, 'octo-org', 'x', 'push', 'branch:main', ?, 'commitguard-app/push', "
                "'{}', 'error', ?, ?, ?, 2, 'internal')",
                (
                    uuid.uuid4().hex,
                    uuid.uuid4().hex,
                    INSTALLATION,
                    repository_id,
                    "c" * 40,
                    clock().timestamp(),
                    clock().timestamp(),
                    clock().timestamp(),
                ),
            )
    governance.run_maintenance()
    alice, ada, sam = sign_in_all()
    paused = ada.get(f"/api/v1/rollouts/{rollout['id']}").data
    assert paused["state"] == "paused"
    assert "safety threshold exceeded" in paused["paused_reason"]

    # 29. Roll back (Phase 7 path): a new version restoring v1.
    rolled_back = alice.post(
        f"/api/v1/rollouts/{rollout['id']}/rollback",
        {"reason": "pilot scans failing", "confirm": True},
    )
    assert rolled_back.status == 200, rolled_back.raw
    assert (rolled_back.data["state"], rolled_back.data["rollback_version"]) == ("rolled_back", 3)

    # 30. Historical scans are unchanged.
    again = alice.get(f"/api/v1/scans/{first_scan['id']}").data
    assert again["organization_policy_version"] == first_detail["organization_policy_version"]
    assert again["effective_policies"] == first_detail["effective_policies"]
    assert again["policy_version"] == first_detail["policy_version"]

    # 31. New scans use the rolled-back effective policy.
    latest = alice.post(f"/api/v1/scans/{blocked['scan']['id']}/rescan").data
    dash.app.run()
    latest_detail = alice.get(f"/api/v1/scans/{latest['scan']}").data
    assert latest_detail["organization_policy_version"] == 3
    assert "organization policy v3" in latest_detail["policy_source"]
    record = json.loads(
        dash.store.query("SELECT governance FROM scan_jobs WHERE job_id = ?", (latest["scan"],))[0][
            "governance"
        ]
    )
    assert record["versions"]["organization_policy"] == 3

    # 32-34. The installation is suspended: repositories at risk, administrators notified.
    suspended = dash.app.deliver(
        "installation", payloads.installation("suspend", INSTALLATION, (PROJECT, *services))
    )
    assert suspended.status == 200
    overview = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    assert overview["posture"] == "at_risk"
    rows = alice.get(f"/api/v1/organizations/{ORG}/security/repositories", limit=100).data
    assert {row["posture"] for row in rows} == {"at_risk"}
    dash.app.service.notifications.run_once()
    critical = [
        n
        for n in alice.get("/api/v1/notifications", limit=100).data
        if n["type"] == "installation_disconnected"
    ]
    assert critical
    assert critical[0]["severity"] == "critical"

    # 35-37. Reconnected and synchronised: enforcement recovers.
    resumed = dash.app.deliver(
        "installation", payloads.installation("unsuspend", INSTALLATION, (PROJECT, *services))
    )
    assert resumed.status == 200
    synced = ada.post(f"/api/v1/github/installations/{INSTALLATION}/sync")
    assert synced.status == 200, synced.raw
    overview = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    assert overview["posture"] != "at_risk"
    [health] = overview["installations"]
    assert (health["state"], health["sync"]) == ("active", "healthy")
    recovered = alice.post(f"/api/v1/scans/{latest['scan']}/rescan").data
    dash.app.run()
    assert alice.get(f"/api/v1/scans/{recovered['scan']}").data["scan"]["result"] == "blocked"

    # 38. Compliance report.
    report = alice.get(f"/api/v1/organizations/{ORG}/reports/compliance", format="json")
    assert report.status == 200
    document = json.loads(report.raw)
    assert document["summary"]["repositories"] == 11
    assert "not a SOC 2" in document["notice"]

    # 39. The audit trail records the governance history.
    for expected in (
        "repository_discovered",
        "repository_group_created",
        "repository_group_members_added",
        "bulk_operation_finished",
        "organization_settings_changed",
        "policy_draft_created",
        "policy_approval_requested",
        "policy_approved",
        "organization_policy_changed",
        "policy_published",
        "policy_simulated",
        "exception_requested",
        "exception_approved",
        "exception_revoked",
        "policy_rollout_started",
        "policy_rollout_paused",
        "organization_policy_rolled_back",
        "policy_rollout_rolled_back",
        "installation_suspended",
        "installation_unsuspended",
        "repositories_synced",
        "report_exported",
    ):
        assert alice.get("/api/v1/audit", type=expected, limit=1).data, expected
