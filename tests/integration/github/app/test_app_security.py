"""Security properties of the GitHub App against a realistic, offline GitHub."""

import io
import json
import logging
import re
from pathlib import Path

import pytest

from commitguard.audit.models import AuditEventType
from commitguard.github.client import HttpResponse, TransportError
from commitguard.github.permissions import REQUIRED_PERMISSIONS
from commitguard.github.storage import JobState
from commitguard.github.webhooks import compute_signature
from commitguard.observability.logging import configure_json_logging
from commitguard.security.secrets import Secret

ZERO = "0" * 40
PWNED = Path("/tmp/commitguard-pwned")  # noqa: S108 - canary that must never be created


@pytest.fixture(autouse=True)
def _no_canary() -> None:
    PWNED.unlink(missing_ok=True)


def _feature(hub, message: str, **commit_args):  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-B", "feature", "main")
    head = hub.dev.commit(message, **commit_args)
    hub.dev.push("--force", "feature")
    return base, head


def _events(app):  # type: ignore[no-untyped-def]
    return [e.type for e in app.service.store.list_audit_events(installation_id=42, limit=500)]


# --------------------------------------------------------------------------- #
# Forks, untrusted metadata, tampering
# --------------------------------------------------------------------------- #
def test_fork_pull_request_is_scanned_with_scoped_read_only_token(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", app.open_pull_request(base, head, fork=True))
    assert app.run() == 1
    assert app.latest_run(head)["conclusion"] == "failure"
    token_requests = [
        body for method, path, body in app.github.requests if path.endswith("/access_tokens")
    ]
    assert token_requests
    for body in token_requests:
        assert body == {"permissions": dict(REQUIRED_PERMISSIONS), "repository_ids": [5001]}
    # The only writes are token minting and the Check Run: nothing touches repository contents.
    writes = [p for m, p, _ in app.github.requests if m in ("POST", "PATCH", "PUT", "DELETE")]
    assert writes
    assert all(re.search(r"/check-runs(/\d+)?$|/access_tokens$", p) for p in writes)


def test_malicious_repository_names_are_rejected_at_the_boundary(app, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    for name in ("$(touch /tmp/commitguard-pwned)", "../../something", "repo; malicious-command"):
        payload = payloads.push("1" * 40, "2" * 40)
        payload["repository"] = {"id": 5001, "full_name": f"octo-org/{name}"}
        assert app.deliver("push", payload).status == 400
    assert app.jobs() == []
    assert not PWNED.exists()


def test_malicious_commit_metadata_stays_data(app, hub) -> None:  # type: ignore[no-untyped-def]
    app.install()
    nasty = "$(touch /tmp/commitguard-pwned) `touch /tmp/commitguard-pwned` && x ; y | z"
    message = (
        f"feat: {nasty}\n\n"
        f"Co-authored-by: {nasty} <noreply@anthropic.com>\n"
        "Co-authored-by: Claude <noreply@anthropic.com>\n"
        f"Signed-off-by: $(id) <`id`@example.com>\n"
    )
    base, head = _feature(hub, message, author="$(touch /tmp/commitguard-pwned) <a@example.com>")
    app.deliver("pull_request", app.open_pull_request(base, head))
    assert app.run() == 1
    run = app.latest_run(head)
    assert run["conclusion"] == "failure"
    text = run["output"]["text"]
    assert "$(touch" not in text  # Markdown-escaped
    assert "\\`" in text or "`touch" not in text
    assert not PWNED.exists()


def test_pull_request_cannot_weaken_its_own_policy(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    weakened = (
        "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"
        "  ai_identity:\n    enabled: false\n"
    )
    base, head = _feature(hub, gh.AI, files={".commitguard.yaml": weakened})
    app.deliver("pull_request", app.open_pull_request(base, head))
    assert app.run() == 1
    run = app.latest_run(head)
    assert run["conclusion"] == "failure"
    summary = run["output"]["summary"]
    assert "Security policy modification detected" in summary
    assert "ai\\_coauthor: block \\-&gt; allow" in summary
    assert AuditEventType.POLICY_MODIFICATION in _events(app)


def test_pull_request_cannot_change_detection_rules(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    rules = "schema_version: 1\nagents: []\n"
    base, head = _feature(
        hub,
        gh.AI,
        files={
            "rules/ai-identities.yaml": rules,
            "src/commitguard/rules/data/ai-identities.yaml": rules,
        },
    )
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    assert app.latest_run(head)["conclusion"] == "failure"


def test_mandatory_policy_overrides_trusted_repository_config(make_app, hub, gh, tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The repository's trusted (base) configuration allows AI co-authors...
    hub.dev.commit(
        "chore: relax policy\n",
        files={".commitguard.yaml": "version: 1\npolicies:\n  ai_coauthor:\n    action: allow\n"},
    )
    hub.dev.push("main")
    policy = tmp_path / "organisation-policy.yaml"
    policy.write_text("version: 1\npolicies:\n  ai_coauthor:\n    action: block\n")
    unmanaged = make_app()
    managed = make_app(mandatory_policy_file=policy)
    base, head = _feature(hub, gh.AI)
    for env in (unmanaged, managed):
        env.install()
        env.deliver("pull_request", env.open_pull_request(base, head))
        env.run()
    # ...so without a mandatory policy it passes, and with one it cannot.
    assert unmanaged.latest_run(head)["conclusion"] == "success"
    managed_run = managed.latest_run(head)
    assert managed_run["conclusion"] == "failure"
    assert "mandatory policy" in managed_run["output"]["summary"]


# --------------------------------------------------------------------------- #
# Authorization and installation lifecycle
# --------------------------------------------------------------------------- #
def test_repository_outside_installation_is_not_accessed(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    other = payloads.REPO.model_copy(update={"id": 7777, "name": "someone-elses"})
    base, head = _feature(hub, gh.AI)
    app.remotes.paths[7777] = hub.bare
    app.deliver("pull_request", payloads.pr(base, head, repository=other))
    assert app.run() == 1
    job = app.jobs()[0]
    assert job.state is JobState.ERROR
    assert job.failure_kind == "authorization"
    assert app.github.check_runs == {}
    assert not (app.data_dir / "mirrors" / "42" / "7777.git").exists()
    assert AuditEventType.AUTHORIZATION_DENIED in _events(app)


def test_spoofed_installation_id_cannot_reach_another_tenant(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    app.github.add_installation(99, (), login="attacker-org")  # attacker's own installation
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", payloads.pr(base, head, installation_id=99))
    app.run()
    jobs = app.service.store.list_jobs(installation_id=99)
    assert [j.state for j in jobs] == [JobState.ERROR]
    assert app.github.check_runs == {}


def test_installation_lifecycle(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", app.open_pull_request(base, head))
    assert app.run() == 1
    assert app.latest_run(head)["conclusion"] == "failure"
    mirror = app.data_dir / "mirrors" / "42" / "5001.git"
    assert mirror.is_dir()

    # Repository removed from the installation.
    del app.github.installations[42].repositories[5001]
    assert (
        app.deliver(
            "installation_repositories", payloads.repositories("removed", (payloads.REPO,))
        ).status
        == 200
    )
    assert not mirror.exists()
    hub.dev.git("checkout", "-q", "feature")
    head2 = hub.dev.commit("feat: more\n")
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head2, action="synchronize"))
    assert app.run() == 1
    assert app.github.runs_for(head2) == []
    assert app.jobs()[0].failure_kind == "authorization"

    # Repository added back: scans work again.
    app.github.installations[42].repositories[5001] = payloads.REPO
    app.deliver("installation_repositories", payloads.repositories("added", (payloads.REPO,)))
    app.deliver("pull_request", app.open_pull_request(base, head2, action="reopened"))
    assert app.run() == 1
    assert app.latest_run(head2)["conclusion"] == "failure"

    # App uninstalled: state disabled, tokens dropped, future events ignored.
    del app.github.installations[42]
    assert (
        app.deliver("installation", payloads.installation("deleted", 42, (payloads.REPO,))).status
        == 200
    )
    assert not (app.data_dir / "mirrors" / "42").exists()
    head3 = hub.dev.commit("feat: after uninstall\n")
    hub.dev.push("feature")
    ignored = app.deliver("pull_request", app.open_pull_request(base, head3, action="synchronize"))
    assert (ignored.status, ignored.body) == (202, {"status": "ignored"})
    assert app.run() == 0
    assert AuditEventType.INSTALLATION_REMOVED in _events(app)


def test_reduced_permissions_never_pass(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    app.github.installations[42].permissions["checks"] = "read"
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", app.open_pull_request(base, head))
    assert app.run() == 1
    job = app.jobs()[0]
    assert (job.state, job.failure_kind) == (JobState.ERROR, "authorization")
    assert app.github.check_runs == {}


def test_permission_revoked_after_check_created_does_not_pass(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, gh.AI)
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.fail(
        "PATCH",
        r"/check-runs/\d+$",
        HttpResponse(403, {}, b'{"message":"Resource not accessible by integration"}'),
    )
    assert app.run() == 1
    run = app.latest_run(head)
    assert run["conclusion"] is None
    assert run["status"] == "queued"  # never success; a required check stays unsatisfied
    assert app.jobs()[0].state is JobState.ERROR


# --------------------------------------------------------------------------- #
# GitHub failures
# --------------------------------------------------------------------------- #
def test_github_outage_during_scan_fails_closed_with_bounded_retries(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, "feat: perfectly clean\n")
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.github.fail("GET", r"/pulls/7$", HttpResponse(503, {}, b"{}"), times=2)
    app.github.fail("GET", r"/repositories/5001$", TransportError("reset"), times=2)
    assert app.run() == 1
    assert app.latest_run(head)["conclusion"] == "success"  # transient errors were retried
    assert len(app.sleeps) == 4

    hub.dev.git("checkout", "-q", "feature")
    head2 = hub.dev.commit("feat: another clean commit\n")
    hub.dev.push("feature")
    app.deliver("pull_request", app.open_pull_request(base, head2, action="synchronize"))
    app.github.fail("PATCH", r"/check-runs/\d+$", HttpResponse(500, {}, b"{}"), times=5)
    app.sleeps.clear()
    assert app.run() == 1
    run = app.latest_run(head2)
    assert run["conclusion"] != "success"
    assert len(app.sleeps) <= 6  # bounded: no infinite retry loop
    assert app.jobs()[0].state is JobState.ERROR


def test_persistent_rate_limit_fails_closed(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base, head = _feature(hub, "feat: clean\n")
    app.deliver("pull_request", app.open_pull_request(base, head))
    limited = HttpResponse(429, {"retry-after": "2"}, b'{"message":"rate limited"}')
    app.github.fail("POST", r"/check-runs$", limited)
    assert app.run() == 1
    job = app.jobs()[0]
    assert (job.state, job.failure_kind) == (JobState.ERROR, "infrastructure")
    assert app.sleeps == [2.0, 2.0]
    assert app.github.check_runs == {}
    assert app.service.metrics.value("github_rate_limits") == 3


def test_missing_commit_on_github_fails_the_check(app, hub, gh) -> None:  # type: ignore[no-untyped-def]
    app.install()
    base = hub.dev.git("rev-parse", "main")
    app.deliver("pull_request", app.open_pull_request(base, "f" * 40))
    assert app.run() == 1
    run = app.latest_run("f" * 40)
    assert run["conclusion"] == "failure"
    assert "could not verify repository policy" in run["output"]["summary"]


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
def test_secrets_never_leak(app, hub, gh, payloads) -> None:  # type: ignore[no-untyped-def]
    stream = io.StringIO()
    configure_json_logging(stream, level=logging.DEBUG)
    try:
        app.install()
        base, head = _feature(hub, gh.AI)
        app.deliver("pull_request", app.open_pull_request(base, head))
        app.run()
        # Force errors whose GitHub responses echo credentials back.
        token_echo = HttpResponse(
            500,
            {},
            json.dumps(
                {"message": "boom Authorization: Bearer ghs_leakleakleakleakleak0000"}
            ).encode(),
        )
        app.github.fail("GET", r"/pulls/7$", token_echo)
        hub.dev.git("checkout", "-q", "feature")
        head2 = hub.dev.commit("feat: two\n")
        hub.dev.push("feature")
        app.deliver("pull_request", app.open_pull_request(base, head2, action="synchronize"))
        app.run()
        bad = app.deliver("pull_request", {"x": 1}, secret=Secret("wrong-secret-wrong-secret"))
        assert bad.status == 401
    finally:
        logging.getLogger("commitguard").handlers.clear()

    minted = list(app.github.tokens)
    assert minted
    pem_lines = [line for line in app.private_key_pem.reveal().splitlines() if "-----" not in line]
    forbidden = [
        payloads.WEBHOOK_SECRET.reveal(),
        compute_signature(payloads.WEBHOOK_SECRET, b"{}"),
        "ghs_leakleakleak",
        *minted,
        *pem_lines[:3],
    ]
    database = b"".join(p.read_bytes() for p in app.data_dir.glob("*.sqlite3*"))
    mirror_config = b"".join(p.read_bytes() for p in app.data_dir.rglob("config"))
    surfaces = {
        "logs": stream.getvalue(),
        "check runs": json.dumps(list(app.github.check_runs.values())),
        "responses": json.dumps(bad.body),
        "jobs": json.dumps([j.model_dump(mode="json") for j in app.jobs()]),
        "audit": json.dumps(
            [
                e.model_dump(mode="json")
                for e in app.service.store.list_audit_events(installation_id=42, limit=500)
            ]
        ),
        "database": database.decode("latin-1"),
        "mirror config": mirror_config.decode("latin-1"),
    }
    for name, text in surfaces.items():
        for secret in forbidden:
            assert secret not in text, f"{secret[:8]}... leaked into {name}"
    assert "installation_id" in stream.getvalue()  # correlation IDs are present
    assert '"delivery_id"' in stream.getvalue()


def test_private_key_errors_are_safe(make_app) -> None:  # type: ignore[no-untyped-def]
    from commitguard.github.app import GitHubAppService
    from commitguard.github.errors import AuthenticationError

    broken = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEsecretmaterialAAAA\n-----END RSA PRIVATE KEY-----\n"
    )
    with pytest.raises(AuthenticationError) as info:
        GitHubAppService.create(
            app_id=1,
            private_key=Secret(broken),
            webhook_secret=Secret("x" * 32),
            data_dir=Path("/nonexistent-never-created"),
        )
    assert "secretmaterial" not in str(info.value)
    assert not Path("/nonexistent-never-created").exists()


def test_large_repository_scans_only_the_pull_request_range(app, hub) -> None:  # type: ignore[no-untyped-def]
    for index in range(150):
        hub.dev.commit(f"chore: history {index}\n")
    hub.dev.push("main")
    app.install()
    base, head = _feature(hub, "feat: one commit\n")
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    assert "| Commits scanned | 1 |" in app.latest_run(head)["output"]["summary"]


def test_commit_limit_fails_closed(make_app, hub) -> None:  # type: ignore[no-untyped-def]
    app = make_app(max_commits=5)
    app.install()
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "-q", "-B", "feature", "main")
    for index in range(8):
        head = hub.dev.commit(f"feat: step {index}\n")
    hub.dev.push("--force", "feature")
    app.deliver("pull_request", app.open_pull_request(base, head))
    app.run()
    run = app.latest_run(head)
    assert run["conclusion"] == "failure"
    assert "more than 5 commits" in run["output"]["summary"]
