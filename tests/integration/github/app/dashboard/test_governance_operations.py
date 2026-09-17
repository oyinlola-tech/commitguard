"""Scheduled scans, posture under installation outages, reports, search, rules, acknowledgement."""

import csv
import io
import json
from datetime import timedelta

from commitguard.github.identifiers import RepositoryRef

ORG = 1001
INSTALLATION = 42
REPO = 5001
ALICE = (501, "alice")
VICTOR = (502, "victor")
SAM = (503, "sam")
ADA = (504, "ada")
BOB = (601, "bob")


def test_scheduled_scan_queues_default_branch_scans_idempotently(dash, hub, clock) -> None:  # type: ignore[no-untyped-def]
    ada = dash.sign_in(ADA)
    head = hub.dev.git("rev-parse", "HEAD")
    dash.github.branch_heads[REPO] = head
    body = {
        "name": "Nightly",
        "target_type": "organization",
        "cadence": "daily",
        "hour": 2,
        "minute": 0,
        "timezone": "UTC",
    }
    assert (
        dash.sign_in(VICTOR).post(f"/api/v1/organizations/{ORG}/scan-schedules", body).status == 403
    )
    assert dash.sign_in(BOB).post(f"/api/v1/organizations/{ORG}/scan-schedules", body).status == 404
    bad = ada.post(f"/api/v1/organizations/{ORG}/scan-schedules", {**body, "hour": 24})
    assert bad.status == 400
    created = ada.post(f"/api/v1/organizations/{ORG}/scan-schedules", body)
    assert created.status == 201, created.raw
    schedule = created.data
    assert schedule["next_run_at"] == "2026-09-02T02:00:00Z"
    assert schedule["repositories_covered"] == 1

    scheduler = dash.app.service.governance.schedules
    assert scheduler.run_due()["started"] == 0  # not due yet
    clock.advance(hours=15)
    first = scheduler.run_due()
    assert (first["started"], first["queued"]) == (1, 1)
    assert scheduler.run_due()["started"] == 0  # a slot runs once
    dash.app.run()
    ada = dash.sign_in(ADA)  # fifteen hours later: a fresh session
    [scan] = ada.get("/api/v1/scans", limit=1).data
    assert scan["trigger"] == "scheduled"
    assert scan["head_sha"] == head
    assert scan["result"] in ("pass", "warning", "blocked")
    detail = ada.get(f"/api/v1/scan-schedules/{schedule['id']}").data
    [run] = detail["runs"]
    assert (run["state"], run["queued"], run["skipped"], run["failed"]) == ("completed", 1, 0, 0)
    assert detail["schedule"]["next_run_at"] == "2026-09-03T02:00:00Z"

    # The next day nothing changed: the head was already scanned with the current policy.
    clock.advance(days=1)
    alice = dash.sign_in(ALICE)
    second = scheduler.run_due()
    assert (second["started"], second["queued"], second["skipped"]) == (1, 0, 1)
    runs = alice.get(f"/api/v1/scan-schedules/{schedule['id']}").data["runs"]
    assert runs[0]["detail"] == {"unchanged": 1}

    # A policy change makes the same head worth re-evaluating.
    assert (
        alice.put(
            f"/api/v1/policies/{ORG}", {"expected_version": 0, "floors": {"bot_identity": "block"}}
        ).status
        == 200
    )
    clock.advance(days=1)
    assert scheduler.run_due()["queued"] == 1

    # Archived repositories are skipped; disabling stops future runs.
    with dash.store.transaction() as db:
        db.execute("UPDATE known_repositories SET archived = 1 WHERE repository_id = ?", (REPO,))
    clock.advance(days=1)
    assert scheduler.run_due()["skipped"] == 1
    alice = dash.sign_in(ALICE)
    disabled = alice.post(f"/api/v1/scan-schedules/{schedule['id']}/disable")
    assert disabled.status == 200, disabled.raw
    assert disabled.data["enabled"] is False
    clock.advance(days=1)
    assert scheduler.run_due()["started"] == 0
    alice = dash.sign_in(ALICE)
    assert alice.get("/api/v1/audit", type="scan_schedule_disabled").data
    assert len(alice.get("/api/v1/audit", type="scheduled_scans_queued").data) >= 3


def test_disconnected_installation_puts_the_organization_at_risk_and_recovers(
    dash, payloads, clock
) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    before = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    assert before["posture"] != "at_risk"

    suspended = dash.app.deliver(
        "installation",
        payloads.installation(
            "suspend", INSTALLATION, (RepositoryRef(id=REPO, owner="octo-org", name="project"),)
        ),
    )
    assert suspended.status == 200
    overview = alice.get(f"/api/v1/organizations/{ORG}/security/overview")
    assert overview.status == 200, overview.raw
    data = overview.data
    assert data["posture"] == "at_risk"
    assert any("suspended" in reason for reason in data["posture_reasons"])
    [installation] = data["installations"]
    assert (installation["state"], installation["sync"]) == ("suspended", "degraded")
    [row] = alice.get(f"/api/v1/organizations/{ORG}/security/repositories").data
    assert row["posture"] == "at_risk"
    assert row["connection"] == "suspended"
    events = alice.get(f"/api/v1/organizations/{ORG}/security/events").data
    critical = [e for e in events if e["type"] == "installation_disconnected"]
    assert critical

    # A security manager acknowledges it; acknowledging resolves nothing.
    sam = dash.sign_in(SAM)
    event_id = critical[0]["id"]
    assert (
        dash.sign_in(VICTOR)
        .post(f"/api/v1/organizations/{ORG}/security/events/{event_id}/acknowledge", {})
        .status
        == 403
    )
    acknowledged = sam.post(
        f"/api/v1/organizations/{ORG}/security/events/{event_id}/acknowledge", {"note": "on it"}
    )
    assert acknowledged.status == 200, acknowledged.raw
    assert "does not resolve" in acknowledged.data["meaning"]
    assert (
        sam.post(f"/api/v1/organizations/{ORG}/security/events/{event_id}/acknowledge", {}).status
        == 409
    )
    assert alice.get(f"/api/v1/organizations/{ORG}/security/overview").data["posture"] == "at_risk"

    resumed = dash.app.deliver(
        "installation",
        payloads.installation(
            "unsuspend", INSTALLATION, (RepositoryRef(id=REPO, owner="octo-org", name="project"),)
        ),
    )
    assert resumed.status == 200
    after = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    assert after["posture"] == before["posture"] == "unknown"  # protection not yet verified
    [row] = alice.get(f"/api/v1/organizations/{ORG}/security/repositories").data
    assert row["connection"] == "connected"
    assert row["posture"] != "at_risk"


def test_a_synchronisation_that_never_finished_is_reported_as_failed(dash, clock) -> None:  # type: ignore[no-untyped-def]
    installations = dash.app.service.installations
    installations._sync_status(INSTALLATION, ORG, "syncing")  # a worker crashed mid-sync
    alice = dash.sign_in(ALICE)
    [health] = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data["installations"]
    assert health["sync"] == "syncing"  # still plausibly running

    clock.advance(minutes=16)
    alice = dash.sign_in(ALICE)
    overview = alice.get(f"/api/v1/organizations/{ORG}/security/overview").data
    [health] = overview["installations"]
    assert (health["sync"], health["sync_detail"]) == (
        "failed",
        "The last synchronisation did not finish.",
    )
    assert overview["posture"] == "at_risk"  # never healthy while synchronisation is broken


def test_compliance_reports_are_point_in_time_and_make_no_certification_claims(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    result = alice.get(f"/api/v1/organizations/{ORG}/reports/compliance", format="json")
    assert result.status == 200, result.raw
    assert result.header("Content-Type") == "application/json"
    assert "attachment" in (result.header("Content-Disposition") or "")
    document = json.loads(result.raw)
    assert document["generated_at"] == "2026-09-01T12:00:00+00:00"
    assert "not a SOC 2" in document["notice"]
    assert document["summary"]["repositories"] == 1
    assert (
        "required repositories satisfy all mandatory controls" in document["summary"]["compliance"]
    )
    assert [row["repository"] for row in document["rows"]] == ["octo-org/project"]

    as_csv = alice.get(f"/api/v1/organizations/{ORG}/reports/coverage", format="csv")
    assert as_csv.status == 200
    rows = list(csv.reader(io.StringIO(as_csv.raw.decode())))
    assert "not a SOC 2" in rows[0][0]
    assert "repository" in rows[2]
    assert alice.get(f"/api/v1/organizations/{ORG}/reports/compliance", format="pdf").status == 400
    assert alice.get(f"/api/v1/organizations/{ORG}/reports/secrets").status == 400
    victor = dash.sign_in(VICTOR)
    assert victor.get(f"/api/v1/organizations/{ORG}/reports/compliance").status == 200
    assert victor.get(f"/api/v1/organizations/{ORG}/reports/policy_changes").status == 404
    assert dash.sign_in(BOB).get(f"/api/v1/organizations/{ORG}/reports/compliance").status == 404
    [exported] = alice.get("/api/v1/audit", type="report_exported", limit=5).data[:1]
    assert exported["data"]["report"] in ("compliance", "coverage")


def test_search_is_tenant_scoped(dash) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    results = alice.get(f"/api/v1/organizations/{ORG}/search", q="project").data
    assert {(r["kind"], r["title"]) for r in results} >= {("repository", "octo-org/project")}
    rules = alice.get(f"/api/v1/organizations/{ORG}/search", q="co-author").data
    assert any(r["kind"] == "rule" and r["id"] == "ai_coauthor" for r in rules)
    assert all(
        "globex" not in r["title"]
        for r in alice.get(f"/api/v1/organizations/{ORG}/search", q="secret").data
    )
    assert alice.get(f"/api/v1/organizations/{ORG}/search", q="x").status == 400
    assert dash.sign_in(BOB).get(f"/api/v1/organizations/{ORG}/search", q="project").status == 404


def test_organization_rules_add_identities_detected_by_real_scans(dash, ops, hub) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    rules = {
        "ai_identities": [
            {
                "id": "acme_agent",
                "display_name": "Acme Coding Agent",
                "names": ["Acme Coding Agent"],
                "emails": ["agent@acme.dev"],
            }
        ]
    }
    path = f"/api/v1/organizations/{ORG}/rules"
    assert (
        dash.sign_in(VICTOR)
        .put(path, {"expected_version": 0, "rules": rules, "reason": "x"})
        .status
        == 403
    )
    assert alice.put(path, {"expected_version": 0, "rules": rules}).status == 400  # reason
    conflict = alice.put(
        path,
        {
            "expected_version": 0,
            "rules": {"ai_identities": [{"id": "x", "display_name": "x", "names": ["Copilot"]}]},
            "reason": "x",
        },
    )
    assert conflict.status == 400
    saved = alice.put(path, {"expected_version": 0, "rules": rules, "reason": "internal agent"})
    assert saved.status == 200, saved.raw
    assert saved.data["version"] == 1
    assert saved.data["rules_version"].endswith("+org-v1")

    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit(
        "feat: generated handler\n\nCo-authored-by: Acme Coding Agent <agent@acme.dev>\n"
    )
    hub.dev.push("feature")
    ops.pull_request(base, head)
    [scan] = alice.get("/api/v1/scans", limit=1).data
    detail = alice.get(f"/api/v1/scans/{scan['id']}").data
    assert detail["scan"]["result"] == "blocked"
    assert detail["rules_version"].endswith("+org-v1")
    [finding] = detail["findings"]
    assert finding["rule_id"] == "ai_coauthor"
    history = alice.get(f"{path}/history").data
    assert [h["version"] for h in history] == [1]
    assert dash.sign_in(BOB).get(path).status == 404
    # Other tenants are unaffected by octo-org's rules.
    assert dash.app.service.governance.rules.compiled(2002)[0] is None


def test_organization_overview_counts_and_trends(dash, ops, hub, clock) -> None:  # type: ignore[no-untyped-def]
    alice = dash.sign_in(ALICE)
    organization = alice.get(f"/api/v1/organizations/{ORG}")
    assert organization.status == 200, organization.raw
    data = organization.data["organization"]
    assert data["login"] == "octo-org"
    assert data["github_url"] == "https://github.com/octo-org"
    assert data["members"] == 4
    assert data["repositories"] == 1
    assert organization.data["settings"]["version"] == 0

    base = hub.dev.git("rev-parse", "HEAD")
    hub.dev.git("checkout", "-q", "-b", "feature")
    head = hub.dev.commit("feat: x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n")
    hub.dev.push("feature")
    ops.pull_request(base, head)
    assert dash.app.service.governance.posture.snapshot_metrics() == 2
    assert dash.app.service.governance.posture.snapshot_metrics() == 0  # at most hourly
    trends = alice.get(f"/api/v1/organizations/{ORG}/security/trends", days=7).data
    [day] = trends["history"]
    assert day["day"] == "2026-09-01"
    assert day["values"]["scans"] == 1
    assert day["values"]["new_violations"] == 1
    [snapshot] = trends["snapshots"]
    assert snapshot["values"]["open_violations"] == 1
    assert "daily snapshots" in trends["snapshot_note"]
    assert alice.get(f"/api/v1/organizations/{ORG}/security/trends", days=0).status == 400
    policies = alice.get(f"/api/v1/organizations/{ORG}/security/policies").data
    assert policies["propagation"]["repositories"] == 1
    assert timedelta
