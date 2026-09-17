# ruff: noqa: E501 - experiment records are one string per field: wrapping them makes
# the recorded evidence harder to read than the line-length rule is worth.
"""Security and reliability experiments against the GitHub App (authoritative layer).

The full stack runs for real - webhook verification, event records, the queue,
the worker, a real Git mirror, detection and policy, Check Runs - against the
offline model of GitHub's API used by the App's integration tests. The model is
the limitation: it reproduces GitHub's documented API behaviour, not github.com.
"""

# ruff: noqa: E501 - experiment records are prose

import io
import json
import sqlite3
import uuid

from commitguard.github.app import WEBHOOK_PATH, create_wsgi_app
from commitguard.github.client import TransportError
from commitguard.github.storage import JobState
from commitguard.github.webhooks import compute_signature
from commitguard.security.secrets import Secret

CLEAN = "feat: clean change\n"


def _feature(hub, message: str, branch: str = "feature") -> tuple[str, str]:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-B", branch, base)
    head = hub.dev.commit(message, files={f"{branch}.txt": message})
    hub.dev.push("--force", branch)
    return base, head


def test_forged_and_tampered_webhooks_are_rejected(app, hub, gh, payloads, observe) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    payload = app.open_pull_request(base, head)
    forged = app.deliver("pull_request", payload, secret=Secret("not-the-webhook-secret-1234"))
    raw = json.dumps(payload).encode()
    tampered_body = raw.replace(b'"opened"', b'"closed"')
    tampered = app.service.handle_webhook(
        {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": compute_signature(payloads.WEBHOOK_SECRET, raw),
            "Content-Type": "application/json",
        },
        tampered_body,
        remote_addr="192.0.2.10",
    )
    unsigned = app.service.handle_webhook(
        {
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "Content-Type": "application/json",
        },
        raw,
        remote_addr="192.0.2.10",
    )
    processed = app.run()
    reasons = [str(r.body) for r in (forged, tampered, unsigned)]
    assert forged.status in (401, 403)
    assert tampered.status in (401, 403)
    assert unsigned.status in (401, 403)
    assert all("signature" in reason.lower() for reason in reasons), reasons
    assert processed == 0
    assert app.github.runs_for(head) == []
    # Control: the same payload, correctly signed, is accepted (so rejection was the signature).
    control = app.deliver("pull_request", payload)
    assert control.body == {"status": "queued"}
    observe(
        experiment="webhook-forgery",
        area="GitHub App webhooks",
        attack="Deliver a pull_request webhook signed with a wrong secret, a correctly signed body altered after signing, and an unsigned body",
        expected="All rejected before parsing; no scan, no check",
        observed=f"HTTP {forged.status} (wrong secret), {tampered.status} (altered body), {unsigned.status} (unsigned), reasons {reasons}; scans processed {processed}; check runs before the control {0}; correctly signed control: {control.body}",
        consequence="An attacker without the webhook secret cannot trigger or suppress scans",
        mitigation="HMAC-SHA256 signature verified in constant time over the raw body",
        limitation="A leaked webhook secret defeats this control; rotate it",
        outcome="prevented",
    )


def test_replayed_and_out_of_order_events_cannot_overwrite_newer_decisions(
    app, hub, gh, observe
) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, old_head = _feature(hub, CLEAN)
    old_event = app.open_pull_request(base, old_head)
    old_delivery = str(uuid.uuid4())
    app.deliver("pull_request", old_event, delivery=old_delivery)
    app.run()
    hub.dev.git("checkout", "-q", "feature")
    new_head = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    app.deliver(
        "pull_request",
        app.open_pull_request(base, new_head, action="synchronize"),
        delivery=str(uuid.uuid4()),
    )
    app.run()
    new_conclusion = app.latest_run(new_head)["conclusion"]

    replay = app.deliver("pull_request", old_event, delivery=old_delivery)  # exact redelivery
    late = app.deliver("pull_request", old_event, delivery=str(uuid.uuid4()))  # old content
    app.run()
    jobs_for_old = [j for j in app.jobs() if j.head_sha == old_head]
    assert new_conclusion == "failure"
    assert replay.body == {"status": "duplicate"}
    assert app.latest_run(new_head)["conclusion"] == "failure"
    old_states = sorted({j.state.value for j in jobs_for_old})
    observe(
        experiment="replay-and-out-of-order",
        area="GitHub App events",
        attack="Redeliver an old webhook (same delivery ID), then deliver the old event again under a new delivery ID after a newer commit was scanned",
        expected="The replay is a duplicate; the late event cannot turn the pull request's newer BLOCK into a PASS",
        observed=f"replay response {replay.body}; late event response {late.body}; newest commit check still {app.latest_run(new_head)['conclusion']}; old commit job states {old_states}",
        consequence="A PASS for an outdated commit cannot hide a violation added later",
        mitigation="Delivery records with payload digests; check ownership per repository, SHA and check name; newest-scan-wins",
        limitation="Checks are per commit SHA: an old commit's own check can still show its old result, which is correct for that SHA",
        outcome="prevented",
    )


def test_github_unavailable_and_revoked_permissions_never_pass(
    app, hub, gh, payloads, observe
) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, CLEAN)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.fail("GET", r"/repositories/5001$", TransportError("connection reset"))
    app.run()
    outage_job = app.jobs()[0]
    outage_runs = app.github.runs_for(head)
    outage_conclusions = [r["conclusion"] for r in outage_runs]
    assert outage_job.state is JobState.ERROR
    assert "success" not in outage_conclusions

    app.github.failures.clear()
    _, head2 = _feature(hub, CLEAN, branch="second")
    # GitHub notifies the App of a permission change; cached tokens must not outlive it.
    app.github.installations[42].permissions["checks"] = "read"
    app.deliver(
        "installation", payloads.installation("new_permissions_accepted", 42, (payloads.REPO,))
    )
    app.deliver("pull_request", app.open_pull_request(base, head2, number=8))
    app.run()
    revoked_job = next(j for j in app.jobs() if j.head_sha == head2)
    assert revoked_job.state is JobState.ERROR
    assert app.github.runs_for(head2) == []
    observe(
        experiment="github-unavailable-and-permissions-revoked",
        area="reliability",
        attack="GitHub's API is unreachable during a scan of a clean commit; separately, the installation's Checks permission is reduced to read",
        expected="Neither produces a successful check (fail closed)",
        observed=f"outage: job {outage_job.state.value} ({outage_job.failure_kind}), check conclusions {outage_conclusions}; revoked: job {revoked_job.state.value} ({revoked_job.failure_kind}), check runs {len(app.github.runs_for(head2))}",
        consequence="A required check stays unsatisfied; merges wait instead of passing unverified code",
        mitigation="Bounded retries, then ERROR; never a success conclusion without a completed evaluation",
        limitation="Availability: while GitHub or the service is down, required checks block merging",
        outcome="prevented",
    )


def test_stale_rerun_and_merge_queue_candidate(app, hub, gh, payloads, observe) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, CLEAN)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    old_run = app.latest_run(head)
    hub.dev.git("checkout", "-q", "feature")
    newer = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, newer, action="synchronize"))
    app.run()
    stale = app.deliver("check_run", payloads.check_run(old_run))
    assert stale.body == {"status": "ignored"}
    assert app.latest_run(newer)["conclusion"] == "failure"

    # The pull request head passed earlier; the merge queue candidate is a different commit.
    hub.dev.git("checkout", "-q", "main")
    ahead = hub.dev.commit(gh.AI, files={"ahead.txt": "x\n"})
    queue_base = hub.dev.git("rev-parse", f"{ahead}~1")
    hub.dev.git("checkout", "-q", "--detach", ahead)
    hub.dev.git("merge", "-q", "--no-ff", "--no-verify", "-m", "Merge #7", head)
    group = hub.dev.git("rev-parse", "HEAD")
    hub.dev.push(f"HEAD:refs/heads/gh-readonly-queue/main/pr-7-{head}")
    hub.dev.git("checkout", "-q", "feature")
    app.deliver("merge_group", payloads.merge_group(queue_base, group))
    app.run()
    group_run = app.latest_run(group)
    assert old_run["conclusion"] == "success"
    assert group_run["conclusion"] == "failure"
    observe(
        experiment="stale-rerun-and-merge-queue",
        area="GitHub checks",
        attack="Re-run the passing check of an outdated commit after a violating commit was pushed; queue a pull request whose own head passed while an AI-attributed change is queued ahead of it",
        expected="The stale re-run is refused; the merge group commit is scanned itself and blocked",
        observed=f"stale re-run response {stale.body}; newest pull request check {app.latest_run(newer)['conclusion']}; pull request head check {old_run['conclusion']}; merge group {group[:12]} check {group_run['conclusion']}",
        consequence="Neither a stale re-run nor a queue candidate can inherit an earlier PASS",
        mitigation="Re-runs only for the newest commit of a pull request or branch; merge_group events scan base..merge-group SHA",
        limitation="Requires the repository's merge queue to require the CommitGuard check",
        outcome="prevented",
    )


def test_mandatory_policy_floor_beats_repository_configuration(
    make_app, hub, gh, tmp_path, observe
) -> None:  # type: ignore[no-untyped-def]
    hub.dev.commit(
        "chore: relax policy\n",
        files={".commitguard.yaml": "version: 1\npolicies:\n  ai_coauthor:\n    enabled: false\n"},
    )
    hub.dev.push("main")
    policy = tmp_path / "mandatory.yaml"
    policy.write_text(
        "version: 1\npolicies:\n  ai_coauthor:\n    action: block\n", encoding="utf-8"
    )
    unmanaged, managed = make_app(), make_app(mandatory_policy_file=policy)
    base, head = _feature(hub, gh.AI)
    for env in (unmanaged, managed):
        env.install()
        env.deliver("pull_request", env.open_pull_request(base, head))
        env.run()
    without_floor = unmanaged.latest_run(head)["conclusion"]
    with_floor = managed.latest_run(head)["conclusion"]
    assert (without_floor, with_floor) == ("success", "failure")
    observe(
        experiment="mandatory-policy-floor",
        area="central policy",
        attack="The repository's merged (trusted) .commitguard.yaml disables ai_coauthor",
        expected="Without a central floor the repository decides; with a mandatory policy the rule still blocks",
        observed=f"without mandatory policy: {without_floor}; with mandatory policy: {with_floor}",
        consequence="Repository administrators cannot opt out of organization requirements",
        mitigation="Mandatory policy floors (service policy, Phase 8 organization/group mandatory entries)",
        limitation="Only repositories scanned by the GitHub App with the floor configured are covered; GitHub Actions uses repository configuration",
        outcome="prevented",
    )


def test_database_unavailable_fails_closed(app, hub, gh, payloads, observe) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, CLEAN)
    payload = app.open_pull_request(base, head)
    store = app.service.store
    delivery = str(uuid.uuid4())

    def broken(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("disk I/O error")

    raw = json.dumps(payload).encode()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": WEBHOOK_PATH,
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "REMOTE_ADDR": "192.0.2.10",
        "HTTP_X_GITHUB_EVENT": "pull_request",
        "HTTP_X_GITHUB_DELIVERY": delivery,
        "HTTP_X_HUB_SIGNATURE_256": compute_signature(payloads.WEBHOOK_SECRET, raw),
    }
    statuses: list[str] = []
    original = store.record_delivery
    store.record_delivery = broken  # type: ignore[method-assign]
    try:
        body = b"".join(
            create_wsgi_app(app.service)(environ, lambda status, headers: statuses.append(status))  # type: ignore[arg-type,return-value]
        )
    finally:
        store.record_delivery = original  # type: ignore[method-assign]
    status = int(statuses[0].split()[0])
    processed = app.run()
    runs = app.github.runs_for(head)
    assert status == 500, body
    assert b"disk" not in body  # internals are not leaked
    assert all(r["conclusion"] != "success" for r in runs)
    redelivered = app.deliver("pull_request", payload, delivery=delivery)
    app.run()
    after = app.latest_run(head)["conclusion"]
    observe(
        experiment="database-unavailable",
        area="reliability",
        attack="The service database fails while a webhook is received",
        expected="The webhook is answered with an error (so GitHub can redeliver); no check is reported as passing; a redelivery after recovery is processed",
        observed=f"HTTP {status} {body.decode()}; scans processed {processed}; checks while down {[r['conclusion'] for r in runs]}; redelivery response {redelivered.body}; check after recovery {after}",
        consequence="No false PASS; the event is not lost if GitHub redelivers it",
        mitigation="Event recorded before processing; errors surface as 5xx; redelivery processed",
        limitation="GitHub does not redeliver automatically: an operator must redeliver from the App's delivery log",
        outcome="prevented",
    )


def test_installation_disconnect_is_visible_and_never_passes(
    app, hub, gh, payloads, observe
) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, CLEAN)
    app.deliver("installation", payloads.installation("suspend", 42, (payloads.REPO,)))
    suspended = app.service.store.get_installation(42)
    app.deliver("pull_request", app.open_pull_request(base, head))
    processed = app.run()
    runs = app.github.runs_for(head)
    audit = [
        e.type.value for e in app.service.store.list_audit_events(installation_id=42, limit=100)
    ]
    notifications = [
        r["type"] for r in app.service.store.query("SELECT type FROM notification_events")
    ]
    assert suspended is not None
    assert all(r["conclusion"] != "success" for r in runs)
    observe(
        experiment="installation-disconnect",
        area="GitHub integration",
        attack="The GitHub App installation is suspended, then a pull request is opened",
        expected="State recorded, audit event and notification created, no passing check",
        observed=f"installation state {suspended.state.value}; scans processed {processed}; checks {[r['conclusion'] for r in runs]}; audit contains installation_suspended: {'installation_suspended' in audit}; notification installation_disconnected: {'installation_disconnected' in notifications}",
        consequence="Enforcement stops while suspended and the dashboard shows repositories at risk instead of protected",
        mitigation="Installation lifecycle events, critical notification, AT RISK posture",
        limitation="While suspended GitHub sends no events and no check is created; only a required check keeps merges blocked",
        outcome="detected",
    )
    assert "installation_suspended" in audit
    assert "installation_disconnected" in notifications
