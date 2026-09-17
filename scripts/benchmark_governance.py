"""Benchmark organization governance at scale (not part of the test suite).

    python scripts/benchmark_governance.py                       # 10,000 repositories
    python scripts/benchmark_governance.py --repositories 1000 --scans 100000 --findings 1000000

Seeds a temporary SQLite state store with one organization, its repositories,
scans, findings, violations and audit events, then times the governance
operations an administrator uses: posture overview, repository matrix,
policy publication and propagation, effective policy resolution, a bulk
operation, a policy simulation, trends and a compliance report.

The results are printed as a table (and as JSON with ``--json``). They describe
this machine and SQLite in WAL mode on local disk; they are not a claim about
any production deployment.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from commitguard.audit.models import Actor  # noqa: E402
from commitguard.controlplane.access import Membership, Principal, Role  # noqa: E402
from commitguard.controlplane.policies import OrganizationPolicyService  # noqa: E402
from commitguard.core.decision import Action  # noqa: E402
from commitguard.github.identifiers import RepositoryRef  # noqa: E402
from commitguard.github.storage import SqliteStateStore  # noqa: E402
from commitguard.governance.service import GovernanceServices  # noqa: E402
from commitguard.services.audit import AuditService  # noqa: E402

ACCOUNT = 1001
INSTALLATION = 42
SESSION = "b" * 64
EVIDENCE = json.dumps(
    [
        {
            "source": "coauthor_trailer",
            "source_label": "Co-authored-by trailer",
            "value": "Claude <noreply@anthropic.com>",
            "line_number": 3,
            "matched": [],
            "notes": [],
        }
    ]
)


def seed(store: SqliteStateStore, repositories: int, scans: int, findings: int, now: float) -> None:
    repos = [
        RepositoryRef(id=100_000 + i, owner="octo-org", name=f"repo-{i:05d}")
        for i in range(repositories)
    ]
    with store.transaction() as db:
        db.execute(
            "INSERT INTO installations (installation_id, account_id, account_login, account_type, "
            "repository_selection, state, permissions, created_at, updated_at) VALUES "
            "(?, ?, 'octo-org', 'Organization', 'all', 'active', '{}', ?, ?)",
            (INSTALLATION, ACCOUNT, now, now),
        )
        db.execute(
            "INSERT INTO users (user_id, login, created_at) VALUES (501, 'alice', ?)", (now,)
        )
        db.execute(
            "INSERT INTO memberships (account_id, user_id, role, granted_by, created_at, "
            "updated_at) VALUES (?, 501, 'owner', 'benchmark', ?, ?)",
            (ACCOUNT, now, now),
        )
        db.execute(
            "INSERT INTO sessions (session_hash, public_id, user_id, created_at, "
            "authenticated_at, last_seen_at, expires_at, user_agent) "
            "VALUES (?, '0123456789abcdef', 501, ?, ?, ?, ?, 'b')",
            (SESSION, now, now, now, now + 86400),
        )
        db.execute(
            "INSERT INTO session_installations (session_hash, installation_id) VALUES (?, ?)",
            (SESSION, INSTALLATION),
        )
        db.executemany(
            "INSERT INTO installation_repositories VALUES (?, ?, ?, ?, ?)",
            [(INSTALLATION, r.id, r.owner, r.name, now) for r in repos],
        )
        db.executemany(
            "INSERT INTO known_repositories (installation_id, repository_id, owner, name, "
            "default_branch, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, 'main', ?, ?)",
            [(INSTALLATION, r.id, r.owner, r.name, now, now) for r in repos],
        )
        db.executemany(
            "INSERT INTO session_repositories VALUES (?, ?, ?)",
            [(SESSION, INSTALLATION, r.id) for r in repos],
        )
        db.executemany(
            "INSERT INTO repository_governance (account_id, repository_id, onboarding, mode, "
            "discovered_at, updated_at) VALUES (?, ?, 'onboarded', 'enforce', ?, ?)",
            [(ACCOUNT, r.id, now, now) for r in repos],
        )
    context = json.dumps(
        {
            "provider": "github",
            "event": "push",
            "event_name": "push",
            "repository": "octo-org/x",
            "ref": "refs/heads/main",
            "default_branch": None,
            "base_sha": None,
            "head_sha": None,
            "before_sha": "b" * 40,
            "after_sha": "a" * 40,
            "pull_request_number": None,
            "from_fork": False,
            "ref_deleted": False,
        }
    )
    job_ids = [uuid.uuid4().hex for _ in range(scans)]
    batch = 20_000
    for start in range(0, scans, batch):
        with store.transaction() as db:
            db.executemany(
                "INSERT INTO scan_jobs (job_id, job_key, installation_id, repository_id, owner, "
                "name, event, group_key, head_sha, check_name, context, state, attempts, "
                "created_at, "
                "updated_at, completed_at, result_action, repository_policies) VALUES (?, ?, ?, ?, "
                "'octo-org', 'x', 'push', ?, ?, 'commitguard-app/push', ?, ?, 1, ?, ?, ?, ?, '{}')",
                [
                    (
                        job_ids[i],
                        job_ids[i],
                        INSTALLATION,
                        repos[i % len(repos)].id,
                        f"branch:b{i % 7}",
                        f"{i:040x}",
                        context,
                        "failed" if i % 3 == 0 else "passed",
                        now - (scans - i) * 30,
                        now - (scans - i) * 30,
                        now - (scans - i) * 30 + 2,
                        "block" if i % 3 == 0 else "allow",
                    )
                    for i in range(start, min(scans, start + batch))
                ],
            )
    for start in range(0, findings, 50_000):
        with store.transaction() as db:
            db.executemany(
                "INSERT INTO findings (job_id, installation_id, repository_id, fingerprint, "
                "commit_sha, rule_id, detector, severity, severity_rank, confidence, action, "
                "policy_id, reason, title, message, remediation, evidence, created_at) VALUES "
                "(?, ?, ?, ?, ?, 'ai_coauthor', 'coauthor', 'high', 3, 'high', 'block', "
                "'ai_coauthor', 'policy', 'AI coauthor detected', 'm', 'r', ?, ?)",
                [
                    (
                        job_ids[i % scans],
                        INSTALLATION,
                        repos[(i % scans) % len(repos)].id,
                        f"{i:064x}",
                        f"{i:040x}",
                        EVIDENCE,
                        now,
                    )
                    for i in range(start, min(findings, start + 50_000))
                ],
            )


def principal(now: datetime, repositories: int) -> Principal:
    del repositories
    return Principal(
        user_id=501,
        login="alice",
        session_hash=SESSION,
        session_public_id="0123456789abcdef",
        authenticated_at=now,
        expires_at=now + timedelta(hours=8),
        memberships={
            ACCOUNT: Membership(ACCOUNT, "octo-org", "Organization", Role.OWNER),
        },
        installations={INSTALLATION: ACCOUNT},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repositories", type=int, default=10_000)
    parser.add_argument("--scans", type=int, default=100_000)
    parser.add_argument("--findings", type=int, default=1_000_000)
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args()

    now = datetime.now(UTC)
    timings: dict[str, float] = {}

    def timed(label: str, call: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        result = call()
        timings[label] = time.perf_counter() - started
        return result

    with tempfile.TemporaryDirectory(prefix="commitguard-benchmark-") as directory:
        store = SqliteStateStore(Path(directory) / "state.db")
        timed(
            "seed data",
            lambda: seed(
                store, options.repositories, options.scans, options.findings, now.timestamp()
            ),
        )
        audit = AuditService([store], now=lambda: now)
        policies = OrganizationPolicyService(store, audit, now=lambda: now)
        governance = GovernanceServices(store, audit, policies, now=lambda: now)
        caller = principal(now, options.repositories)
        ids = sorted(
            int(r["repository_id"])
            for r in store.query("SELECT repository_id FROM known_repositories")
        )

        overview = timed("security overview", lambda: governance.posture.overview(caller, ACCOUNT))
        if overview.repositories != options.repositories:
            raise SystemExit(f"expected {options.repositories} repositories in the overview")
        timed(
            "repository matrix page (sorted by violations)",
            lambda: governance.posture.matrix(
                caller, ACCOUNT, filters={"sort": "violations"}, offset=0, limit=100
            ),
        )
        timed(
            "repository matrix search",
            lambda: governance.posture.matrix(
                caller, ACCOUNT, filters={"q": "repo-0042"}, offset=0, limit=100
            ),
        )
        timed(
            "publish organization policy",
            lambda: policies.update(
                account_id=ACCOUNT,
                actor=Actor.user(501, "alice"),
                authenticated_at=now,
                expected_version=0,
                floors={"ai_coauthor": Action.BLOCK},
                reason="benchmark",
                confirm_weakening=False,
            ),
        )
        timed(
            f"propagate {options.repositories:,} effective policies",
            lambda: governance.resolver.propagate(limit=options.repositories),
        )
        timed(
            "resolve 1,000 repositories (cached)",
            lambda: [governance.resolver.for_repository(ACCOUNT, r) for r in ids[:1000]],
        )
        group = governance.groups.create(caller, ACCOUNT, name="All", description=None)
        operation = timed(
            f"queue bulk operation ({min(5000, len(ids)):,} items)",
            lambda: governance.bulk.create(
                caller,
                ACCOUNT,
                operation_type="add_to_group",
                repository_ids=ids[:5000],
                parameters={"group_id": group.id},
                idempotency_key=None,
                confirm=False,
            ),
        )
        timed(
            f"process bulk operation ({min(5000, len(ids)):,} items)",
            lambda: governance.bulk.run_pending(budget=5000),
        )
        if governance.bulk.get(caller, operation.id).status != "completed":
            raise SystemExit("the bulk operation did not complete")
        from commitguard.controlplane.policies import ORGANIZATION_TARGET

        simulation = governance.simulations.create(
            caller,
            ACCOUNT,
            target=ORGANIZATION_TARGET,
            document='{"ai_coauthor":"warn"}',
            draft_id=None,
            period_days=90,
        )
        timed("policy simulation (bounded)", governance.simulations.run_pending)
        result = governance.simulations.get(caller, simulation.id).result
        timed("trends (90 days)", lambda: governance.posture.trends(caller, ACCOUNT, days=90))
        timed(
            "compliance report (CSV)",
            lambda: governance.posture.report(caller, ACCOUNT, kind="compliance", fmt="csv"),
        )
        timed("metrics snapshot", governance.posture.snapshot_metrics)
        store.close()

    summary = {
        "repositories": options.repositories,
        "scans": options.scans,
        "findings": options.findings,
        "timings_ms": {k: round(v * 1000, 1) for k, v in timings.items()},
        "simulation": result.model_dump(exclude={"most_affected", "disclaimer"})
        if result
        else None,
    }
    if options.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"organization governance: {options.repositories:,} repositories, "
            f"{options.scans:,} scans, {options.findings:,} findings"
        )
        for label, seconds in timings.items():
            print(f"  {label:<52} {seconds * 1000:10.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
