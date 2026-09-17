"""Pure governance helpers: settings weakening, schedules, stages, posture, drift, reports."""

from datetime import UTC, datetime

import pytest

from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.controlplane.errors import InputValidationError
from commitguard.controlplane.policies import (
    canonical_document,
    parse_document,
    policy_changes,
    policy_diff,
)
from commitguard.controlplane.views import (
    AppConnection,
    OrganizationRef,
    ProtectionStatus,
    RepositorySummary,
)
from commitguard.core.decision import Action
from commitguard.core.result import Severity
from commitguard.governance.posture import _csv_cell, drift_from, repository_posture
from commitguard.governance.rollouts import parse_stages
from commitguard.governance.rules import parse_document as parse_rules
from commitguard.governance.schedules import next_occurrence
from commitguard.governance.settings import OrganizationSettings, weakening_changes
from commitguard.policies.governance import (
    GovernanceInputs,
    PolicyLayer,
    PolicyLevel,
    RepositoryMode,
    RuleRequirement,
    resolve_policy,
)


# -- policy documents ------------------------------------------------------ #
def test_document_keeps_the_phase_6_format_for_mandatory_entries() -> None:
    assert canonical_document({"ai_coauthor": Action.BLOCK}, {}) == '{"ai_coauthor":"block"}'
    document = canonical_document({"ai_coauthor": Action.BLOCK}, {"bot_identity": Action.WARN})
    assert document == (
        '{"ai_coauthor":"block","bot_identity":{"action":"warn","enforcement":"default"}}'
    )
    assert parse_document(document) == (
        {"ai_coauthor": Action.BLOCK},
        {"bot_identity": Action.WARN},
    )
    with pytest.raises(InputValidationError, match="either mandatory or a default"):
        canonical_document({"ai_coauthor": Action.BLOCK}, {"ai_coauthor": Action.WARN})
    for bad in (
        '{"x":"block"}',
        '{"ai_coauthor":"allow"}',
        '{"ai_coauthor":{"action":"warn"}}',
        "[]",
    ):
        with pytest.raises(ValueError, match=r"policy|entry|object|allow|warn"):
            parse_document(bad)


def test_default_changes_are_weakening_only_below_the_fallback() -> None:
    [change] = policy_changes({}, {}, {}, {"ai_coauthor": Action.WARN})
    assert change.enforcement == "default"
    assert change.weakening is True  # built-in default for ai_coauthor is block
    [change] = policy_changes({}, {}, {"ai_coauthor": Action.BLOCK}, {})
    assert change.weakening is False  # falls back to the built-in block
    [change] = policy_changes({"bot_identity": Action.WARN}, {}, {}, {})
    assert change.weakening is True  # a removed floor always weakens
    diff = policy_diff(1, {"bot_identity": Action.WARN}, 2, {}, {}, {"ai_trailer": Action.ALLOW})
    assert diff.weakening is True
    assert [e.policy_id for e in diff.removed] == ["bot_identity"]
    assert [(e.policy_id, e.enforcement) for e in diff.added] == [("ai_trailer", "default")]


# -- settings -------------------------------------------------------------- #
def test_settings_weakening_is_detected_per_control() -> None:
    strict = OrganizationSettings(
        security_baseline={"ai_coauthor": Action.BLOCK},
        require_policy_approval=True,
    )
    relaxed = strict.model_copy(
        update={
            "security_baseline": {"ai_coauthor": Action.WARN},
            "require_policy_approval": False,
            "require_separate_approver": False,
            "allow_permanent_exceptions": True,
            "exception_max_days": 365,
            "exception_approval_min_severity": Severity.CRITICAL,
            "default_onboarding_mode": RepositoryMode.MONITOR,
            "rollout_auto_pause": False,
        }
    )
    changes = weakening_changes(strict, relaxed)
    assert len(changes) == 8
    assert weakening_changes(relaxed, strict) == []


def test_settings_validation() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        OrganizationSettings(security_baseline={"nope": Action.BLOCK})
    with pytest.raises(ValueError, match="warn or block"):
        OrganizationSettings(security_baseline={"ai_coauthor": Action.ALLOW})
    with pytest.raises(ValueError, match="unknown time zone"):
        OrganizationSettings(timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        OrganizationSettings(exception_max_days=0)
    assert OrganizationSettings(exception_warning_days=(1, 7, 3, 7)).exception_warning_days == (
        7,
        3,
        1,
    )


# -- schedules ------------------------------------------------------------- #
def test_daily_and_weekly_occurrences_follow_the_local_time_zone() -> None:
    after = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert next_occurrence("daily", 2, 0, None, "UTC", after) == datetime(
        2026, 9, 2, 2, 0, tzinfo=UTC
    )
    # 02:00 in London during BST is 01:00 UTC.
    assert next_occurrence("daily", 2, 0, None, "Europe/London", after) == datetime(
        2026, 9, 2, 1, 0, tzinfo=UTC
    )
    # Tuesday 1 September 2026 -> next Monday 7 September.
    assert next_occurrence("weekly", 9, 30, 0, "UTC", after) == datetime(
        2026, 9, 7, 9, 30, tzinfo=UTC
    )
    # Strictly after: the same minute is the next day.
    at = datetime(2026, 9, 2, 2, 0, tzinfo=UTC)
    assert next_occurrence("daily", 2, 0, None, "UTC", at) == datetime(2026, 9, 3, 2, 0, tzinfo=UTC)


def test_daylight_saving_change_keeps_the_local_hour() -> None:
    # Clocks go back in London on 25 October 2026: 02:00 local is 02:00 UTC afterwards.
    after = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    assert next_occurrence("daily", 2, 0, None, "Europe/London", after) == datetime(
        2026, 10, 26, 2, 0, tzinfo=UTC
    )


# -- rollouts -------------------------------------------------------------- #
def test_stages_are_validated_and_completed_to_100_percent() -> None:
    stages = parse_stages([{"repositories": [3, 1, 3]}, {"percent": 50}])
    assert stages[0] == {"name": "Stage 1", "kind": "repositories", "repositories": [1, 3]}
    assert stages[-1]["percent"] == 100
    for bad in (
        [],
        [{}],
        [{"percent": 0}],
        [{"percent": 60}, {"percent": 40}],
        [{"repositories": []}],
        [{"repositories": ["x"]}],
        [{"percent": 10}] * 11,
    ):
        with pytest.raises(InputValidationError):
            parse_stages(bad)


# -- organization rules ---------------------------------------------------- #
def test_organization_rules_are_data_only() -> None:
    document = parse_rules(
        {
            "bot_identities": [
                {"id": "release_bot", "display_name": "Release", "github_logins": ["relbot"]}
            ]
        }
    )
    assert document.bot_identities[0].id == "release_bot"
    for bad, message in (
        ({"ai_identities": [{"id": "x", "display_name": "x", "names": ["Claude"]}]}, "conflict"),
        ({"ai_identities": [{"id": "x", "display_name": "x", "regex": ".*"}]}, "Extra inputs"),
        ({"ai_identities": [{"id": "x", "display_name": "x"}]}, "needs names"),
        ({"script": "import os"}, "Extra inputs"),
        ({"ai_identities": [{"id": "Bad-Id", "display_name": "x", "names": ["n"]}]}, "pattern"),
        (
            {"ai_identities": [{"id": "x", "display_name": "x", "names": ["a\x1b[31m"]}]},
            "control characters",
        ),
    ):
        with pytest.raises(InputValidationError, match=message):
            parse_rules(bad)


# -- posture --------------------------------------------------------------- #
def _summary(**changes: object) -> RepositorySummary:
    base = {
        "id": 1,
        "installation_id": 1,
        "organization": OrganizationRef(id=1, login="o", type="Organization"),
        "owner": "o",
        "name": "r",
        "full_name": "o/r",
        "github_url": "https://github.com/o/r",
        "default_branch": "main",
        "protection": ProtectionStatus.PROTECTED,
        "protection_reason": "A CommitGuard check is required.",
        "app_connection": AppConnection.CONNECTED,
        "monitoring_enabled": True,
        "last_scan": None,
        "open_violations": 0,
        "open_warnings": 0,
        "critical_open": 0,
    }
    return RepositorySummary(**{**base, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "mode", "state", "exceptions", "expected"),
    [
        ({}, RepositoryMode.ENFORCE, "up_to_date", 0, "secure"),
        (
            {"app_connection": AppConnection.SUSPENDED},
            RepositoryMode.ENFORCE,
            "up_to_date",
            0,
            "at_risk",
        ),
        ({"monitoring_enabled": False}, RepositoryMode.ENFORCE, "up_to_date", 0, "unprotected"),
        (
            {"protection": ProtectionStatus.UNPROTECTED},
            RepositoryMode.ENFORCE,
            "up_to_date",
            0,
            "unprotected",
        ),
        ({}, RepositoryMode.ENFORCE, "error", 0, "at_risk"),
        ({"critical_open": 2}, RepositoryMode.ENFORCE, "up_to_date", 0, "at_risk"),
        ({}, RepositoryMode.MONITOR, "up_to_date", 0, "at_risk"),
        ({}, RepositoryMode.ENFORCE, "up_to_date", 1, "at_risk"),
        (
            {"protection": ProtectionStatus.UNKNOWN},
            RepositoryMode.ENFORCE,
            "up_to_date",
            0,
            "unknown",
        ),
        # The first matching rule decides: a disconnected repository is at risk, not unknown.
        (
            {"app_connection": AppConnection.DISCONNECTED, "protection": ProtectionStatus.AT_RISK},
            RepositoryMode.MONITOR,
            "error",
            3,
            "at_risk",
        ),
    ],
)
def test_repository_posture_rules(changes, mode, state, exceptions, expected) -> None:  # type: ignore[no-untyped-def]
    posture, reasons = repository_posture(
        summary=_summary(**changes), mode=mode, policy_state=state, high_exceptions=exceptions
    )
    assert posture == expected
    assert reasons


def test_drift_is_explained_not_just_flagged() -> None:
    inputs = GovernanceInputs(
        layers=(
            PolicyLayer(
                level=PolicyLevel.ORGANIZATION,
                label="organization policy v10",
                rules={"ai_coauthor": RuleRequirement(action=Action.BLOCK)},
            ),
        )
    )
    effective = resolve_policy(
        inputs,
        [
            CommitGuardConfig(
                version=1, policies={"ai_coauthor": PolicyOverride(action=Action.WARN)}
            )
        ],
    )
    status, [difference] = drift_from(effective)
    assert status == "drift"
    assert (difference.requested, difference.required, difference.effective) == (
        "warn",
        "block",
        "block",
    )
    assert difference.required_by == "organization policy v10"
    customized = resolve_policy(
        GovernanceInputs(),
        [
            CommitGuardConfig(
                version=1, policies={"bot_identity": PolicyOverride(action=Action.BLOCK)}
            )
        ],
    )
    assert drift_from(customized) == ("customized", ())
    assert drift_from(resolve_policy(GovernanceInputs(), [])) == ("compliant", ())
    assert drift_from(None) == ("unknown", ())


def test_csv_cells_cannot_become_spreadsheet_formulas() -> None:
    assert _csv_cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_cell("octo-org/project") == "octo-org/project"
    assert _csv_cell(None) == ""
    assert _csv_cell({"a": 1}) == '{"a": 1}'
