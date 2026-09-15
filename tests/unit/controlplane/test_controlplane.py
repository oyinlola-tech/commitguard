"""Unit tests for the control plane building blocks."""

import sqlite3
from datetime import UTC, datetime

import pytest

from commitguard.api.settings import Environment, load_dashboard_settings, normalize_origin
from commitguard.audit.models import AuditEvent, AuditEventType
from commitguard.controlplane.errors import InputValidationError
from commitguard.controlplane.identity import csrf_token_for, pkce_challenge, safe_return_path
from commitguard.controlplane.pagination import (
    decode_cursor,
    encode_cursor,
    like_pattern,
    offset_cursor,
    parse_limit,
    parse_search,
    parse_timestamp,
)
from commitguard.controlplane.policies import policy_changes, validate_floors
from commitguard.controlplane.queries import protection_for
from commitguard.controlplane.results import scan_result_label
from commitguard.controlplane.rules import CATALOG, CATALOG_BY_ID, list_rules, rule_detail
from commitguard.controlplane.views import AppConnection, ProtectionStatus
from commitguard.core.decision import Action
from commitguard.detectors.registry import builtin_registry
from commitguard.github.errors import AppConfigurationError
from commitguard.github.storage import _SCHEMA_V1, SCHEMA_VERSION, SqliteStateStore
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.rules.loader import load_builtin_rules

NOW = datetime(2026, 9, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Rules: the catalogue describes, never redefines, detection
# --------------------------------------------------------------------------- #
def test_rule_catalogue_matches_detectors_and_policies() -> None:
    registry = builtin_registry(load_builtin_rules())
    assert {entry.rule_id for entry in CATALOG} == set(registry.rules()) == set(DEFAULT_POLICIES)
    for entry in CATALOG:
        assert entry.rule_id in registry.get(entry.detector).rules


def test_rule_catalogue_severities_match_emitted_findings(commit_cases) -> None:  # type: ignore[no-untyped-def]
    from commitguard.core.context import CommitContext

    registry = builtin_registry(load_builtin_rules())
    seen: dict[str, set[str]] = {}
    for case in commit_cases:
        for detector in registry.all():
            for finding in detector.detect(CommitContext(commit=case.commit)):
                seen.setdefault(finding.rule_id, set()).add(finding.severity.value)
    assert seen, "fixtures produced no findings"
    for rule_id, severities in seen.items():
        assert severities == {CATALOG_BY_ID[rule_id].severity.value}, rule_id


def test_rules_views_are_bundled_and_not_editable() -> None:
    rules = list_rules()
    assert [r.id for r in rules] == [e.rule_id for e in CATALOG]
    assert {(r.source, r.trusted, r.status) for r in rules} == {("bundled", True, "active")}
    detail = rule_detail("ai_coauthor")
    assert detail is not None and detail.editable is False
    assert detail.remediation[-1].startswith("CommitGuard does not rewrite Git history")
    assert all(f.entries > 0 for f in detail.data_files)
    assert rule_detail("unknown") is None


# --------------------------------------------------------------------------- #
# Schema migration
# --------------------------------------------------------------------------- #
def test_phase5_database_is_upgraded_in_place(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA_V1)
    db.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    db.execute(
        "INSERT INTO installations VALUES (42, 1001, 'octo-org', 'Organization', 'selected', "
        "'active', '{}', 1.0, 1.0)"
    )
    db.execute("INSERT INTO installation_repositories VALUES (42, 5001, 'octo-org', 'project', 1.0)")
    event = AuditEvent(type=AuditEventType.SCAN_PASSED, occurred_at=NOW, installation_id=42)
    db.execute(
        "INSERT INTO audit_events VALUES (?, ?, ?, 42, NULL, ?)",
        (event.event_id, NOW.timestamp(), event.type.value, event.model_dump_json()),
    )
    db.commit()
    db.close()

    store = SqliteStateStore(path)
    try:
        assert store.query("SELECT value FROM meta")[0]["value"] == str(SCHEMA_VERSION)
        [repository] = store.query("SELECT * FROM known_repositories")
        assert (repository["repository_id"], repository["removed_at"]) == (5001, None)
        assert store.query("SELECT account_id FROM audit_events")[0]["account_id"] == 1001
        assert store.list_audit_events(installation_id=42)[0].event_id == event.event_id
    finally:
        store.close()
    # Reopening does not migrate twice.
    SqliteStateStore(path).close()


def test_newer_schema_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from commitguard.exceptions.service import InfrastructureError

    path = tmp_path / "state.sqlite3"
    SqliteStateStore(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    with pytest.raises(InfrastructureError, match="unsupported schema version"):
        SqliteStateStore(path)


# --------------------------------------------------------------------------- #
# Input parsing
# --------------------------------------------------------------------------- #
def test_limits_and_cursors() -> None:
    assert parse_limit(None) == 25
    assert parse_limit("100") == 100
    for bad in ("0", "101", "-1", "1e3", "abc", "٣"):
        with pytest.raises(InputValidationError):
            parse_limit(bad)
    cursor = encode_cursor([12.5, "abc"])
    assert decode_cursor(cursor, (float, str)) == [12.5, "abc"]
    assert decode_cursor(encode_cursor([3, 4]), (float, int)) == [3, 4]
    for bad in ("!!!", encode_cursor(["x"]), encode_cursor([True]), "A" * 600, encode_cursor([1, 2])):
        with pytest.raises(InputValidationError):
            decode_cursor(bad, (int,))
    assert offset_cursor(None) == 0
    with pytest.raises(InputValidationError):
        offset_cursor(encode_cursor([-5]))


def test_search_and_like_escaping() -> None:
    assert parse_search("  octo   org ") == "octo org"
    assert parse_search("") is None
    with pytest.raises(InputValidationError):
        parse_search("x" * 101)
    with pytest.raises(InputValidationError):
        parse_search("a\x1bb")
    assert like_pattern("100%_\\") == "%100\\%\\_\\\\%"


def test_timestamps() -> None:
    assert parse_timestamp("2026-09-01", "from") == datetime(2026, 9, 1, tzinfo=UTC)
    with pytest.raises(InputValidationError):
        parse_timestamp("yesterday", "from")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "/dashboard"),
        ("/violations?status=open", "/violations?status=open"),
        ("//evil.example", "/dashboard"),
        ("https://evil.example/", "/dashboard"),
        ("/\\evil.example", "/dashboard"),
        ("/api/v1/auth/logout", "/dashboard"),
        ("javascript:alert(1)", "/dashboard"),
        ("/x\r\nSet-Cookie: a=b", "/dashboard"),
        ("/" + "a" * 600, "/dashboard"),
    ],
)
def test_return_paths(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert safe_return_path(value) == expected


def test_csrf_and_pkce_derivations() -> None:
    assert csrf_token_for("token-a") != csrf_token_for("token-b")
    assert len(csrf_token_for("x")) == 64
    # RFC 7636 appendix B test vector.
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


# --------------------------------------------------------------------------- #
# Status and policy rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("state", "action", "label"),
    [
        ("queued", None, "queued"),
        ("running", None, "running"),
        ("passed", "allow", "pass"),
        ("passed", "warn", "warning"),
        ("failed", "block", "blocked"),
        ("error", None, "error"),
        ("cancelled", None, "cancelled"),
        ("unexpected", None, "error"),
    ],
)
def test_scan_result_labels(state, action, label) -> None:  # type: ignore[no-untyped-def]
    assert scan_result_label(state, action) == label


def test_protection_needs_evidence() -> None:
    def status(**overrides):  # type: ignore[no-untyped-def]
        values = {
            "app": AppConnection.CONNECTED,
            "monitoring_enabled": True,
            "latest_failure_kind": None,
            "branch_protection": None,
            "detail": None,
        }
        values.update(overrides)
        return protection_for(**values)[0]

    assert status() is ProtectionStatus.UNKNOWN  # installed is not protected
    assert status(branch_protection="required") is ProtectionStatus.PROTECTED
    assert status(branch_protection="not_required") is ProtectionStatus.UNPROTECTED
    assert status(branch_protection="unknown") is ProtectionStatus.UNKNOWN
    assert status(branch_protection="required", monitoring_enabled=False) is ProtectionStatus.UNPROTECTED
    assert status(branch_protection="required", app=AppConnection.SUSPENDED) is ProtectionStatus.UNPROTECTED
    assert status(branch_protection="required", app=AppConnection.DISCONNECTED) is ProtectionStatus.UNPROTECTED
    assert (
        status(branch_protection="required", latest_failure_kind="configuration")
        is ProtectionStatus.CONFIGURATION_ERROR
    )


def test_policy_changes_classify_weakening() -> None:
    changes = policy_changes(
        {"ai_coauthor": Action.BLOCK, "bot_identity": Action.WARN, "ai_trailer": Action.WARN},
        {"ai_coauthor": Action.WARN, "ai_identity": Action.BLOCK, "ai_trailer": Action.BLOCK},
    )
    assert [(c.policy_id, c.weakening) for c in changes] == [
        ("ai_coauthor", True),
        ("ai_identity", False),
        ("ai_trailer", False),
        ("bot_identity", True),
    ]
    with pytest.raises(InputValidationError):
        validate_floors({"ai_coauthor": "allow"})
    assert validate_floors({"ai_coauthor": None, "bot_identity": "warn"}) == {
        "bot_identity": Action.WARN
    }


def test_mandatory_floors_combine_with_the_service_policy(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from commitguard.audit.models import Actor
    from commitguard.config.sources import load_mandatory_policy
    from commitguard.controlplane.policies import OrganizationPolicyService
    from commitguard.policies.loader import build_policy_set
    from commitguard.policies.mandatory import apply_mandatory_policies
    from commitguard.config.schema import CommitGuardConfig, PolicyOverride
    from commitguard.github.identifiers import AccountType
    from commitguard.github.storage import InstallationRecord, InstallationState
    from commitguard.services.audit import AuditService

    service_file = tmp_path / "mandatory.yaml"
    service_file.write_text("version: 1\npolicies:\n  ai_coauthor:\n    action: warn\n")
    store = SqliteStateStore(":memory:")
    store.upsert_installation(
        InstallationRecord(
            installation_id=42,
            account_id=1001,
            account_login="octo-org",
            account_type=AccountType.ORGANIZATION,
            repository_selection="all",
            state=InstallationState.ACTIVE,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = OrganizationPolicyService(
        store, AuditService([store]), service_policy=load_mandatory_policy(service_file), now=lambda: NOW
    )
    mandatory, version = service.mandatory_for_installation(42)
    assert version is None and mandatory is not None
    service.update(
        account_id=1001,
        actor=Actor.user(1, "alice"),
        authenticated_at=NOW,
        expected_version=0,
        floors={"ai_coauthor": Action.BLOCK, "bot_identity": Action.WARN},
        reason=None,
        confirm_weakening=False,
    )
    mandatory, version = service.mandatory_for_installation(42)
    assert version == 1 and mandatory is not None
    assert "organization policy v1" in mandatory.description
    repository = CommitGuardConfig(
        version=1,
        policies={"ai_coauthor": PolicyOverride(enabled=False), "bot_identity": PolicyOverride(action=Action.ALLOW)},
    )
    effective = apply_mandatory_policies(build_policy_set(repository), mandatory.config)
    assert (effective["ai_coauthor"].enabled, effective["ai_coauthor"].action) == (True, Action.BLOCK)
    assert effective["bot_identity"].action is Action.WARN
    unknown, unknown_version = service.mandatory_for_installation(999)
    assert unknown_version is None and unknown is not None
    assert unknown.description == "service policy mandatory.yaml"
    assert set(unknown.config.policies) == {"ai_coauthor"}


# --------------------------------------------------------------------------- #
# Dashboard settings
# --------------------------------------------------------------------------- #
BASE_ENV = {
    "COMMITGUARD_DASHBOARD_URL": "https://commitguard.example.com",
    "COMMITGUARD_GITHUB_CLIENT_ID": "Iv23liExample",
    "COMMITGUARD_GITHUB_CLIENT_SECRET": "s" * 40,
}


def test_dashboard_settings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = load_dashboard_settings(BASE_ENV)
    assert settings.redirect_uri == "https://commitguard.example.com/api/v1/auth/callback"
    assert settings.environment is Environment.PRODUCTION
    assert "s" * 40 not in repr(settings)
    secret_file = tmp_path / "client-secret"
    secret_file.write_text("f" * 40 + "\n")
    from_file = {**BASE_ENV, "COMMITGUARD_GITHUB_CLIENT_SECRET_FILE": str(secret_file)}
    del from_file["COMMITGUARD_GITHUB_CLIENT_SECRET"]
    assert load_dashboard_settings(from_file).client_secret.reveal() == "f" * 40
    local = {**BASE_ENV, "COMMITGUARD_DASHBOARD_URL": "http://localhost:5173", "COMMITGUARD_ENV": "development"}
    assert load_dashboard_settings(local).origin == "http://localhost:5173"


@pytest.mark.parametrize(
    "overrides",
    [
        {"COMMITGUARD_DASHBOARD_URL": "http://commitguard.example.com"},
        {"COMMITGUARD_DASHBOARD_URL": "http://localhost:5173"},  # production
        {"COMMITGUARD_DASHBOARD_URL": "https://user:pw@commitguard.example.com"},
        {"COMMITGUARD_DASHBOARD_URL": "https://commitguard.example.com/app"},
        {"COMMITGUARD_GITHUB_CLIENT_SECRET": "short"},
        {"COMMITGUARD_GITHUB_CLIENT_ID": "id with spaces"},
        {"COMMITGUARD_ENV": "staging"},
        {"COMMITGUARD_DASHBOARD_STATIC_DIR": "relative/dist"},
        {"COMMITGUARD_DASHBOARD_ALLOWED_ORIGINS": "https://a.example, *"},
    ],
)
def test_invalid_dashboard_settings_fail_closed(overrides) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(AppConfigurationError) as error:
        load_dashboard_settings({**BASE_ENV, **overrides})
    assert "s" * 40 not in str(error.value)


def test_origin_normalisation() -> None:
    assert normalize_origin("https://Example.COM:8443/", Environment.PRODUCTION, "X") == (
        "https://example.com:8443"
    )
