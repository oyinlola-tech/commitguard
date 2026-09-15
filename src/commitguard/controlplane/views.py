"""The dashboard API's resource models (the ``data`` of API responses).

These are the public contract of ``/api/v1``. They are built from database
rows by the query services and deliberately differ from them: internal columns
(leases, job keys, delivery IDs, session hashes) never appear here, and every
status is one value of a closed vocabulary decided by the server. The
dashboard renders these values; it never derives a security decision itself.

Status vocabulary
=================

=====================  ================================================================
Field                  Values
=====================  ================================================================
scan ``result``        ``queued`` ``running`` ``pass`` ``warning`` ``blocked``
                       ``error`` ``cancelled`` ``stale``
scan ``trigger``       ``push`` ``pull_request`` ``merge_group`` ``manual`` ``rerun``
                       ``retry``
violation ``status``   ``open`` ``acknowledged`` ``resolved``
``protection``         ``protected`` ``at_risk`` ``unprotected`` ``configuration_error``
                       ``unknown``
merge queue            ``enabled`` ``not_enabled`` ``unknown``
notification ``state`` ``unread`` ``read`` ``archived``
GitHub App             ``connected`` ``suspended`` ``disconnected``
GitHub Actions         ``detected`` ``not_detected`` ``unknown``
required check         ``required`` ``not_required`` ``unknown``
local hooks            ``not_verifiable``
integration            ``connected`` ``action_required`` ``disconnected``
severity               ``info`` ``low`` ``medium`` ``high`` ``critical`` (core model)
policy action          ``allow`` ``warn`` ``block`` (core model)
=====================  ================================================================
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.core.result import Severity


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #
class ScanResultStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASS = "pass"  # noqa: S105 - status name, not a password
    WARNING = "warning"
    BLOCKED = "blocked"
    ERROR = "error"
    CANCELLED = "cancelled"
    STALE = "stale"  # superseded before it could publish: a newer execution owns the check


class ViolationStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ProtectionStatus(StrEnum):
    PROTECTED = "protected"
    AT_RISK = "at_risk"  # the GitHub App lost access: enforcement may no longer run
    UNPROTECTED = "unprotected"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN = "unknown"


class AppConnection(StrEnum):
    CONNECTED = "connected"
    SUSPENDED = "suspended"
    DISCONNECTED = "disconnected"


class ActionsStatus(StrEnum):
    DETECTED = "detected"
    NOT_DETECTED = "not_detected"
    UNKNOWN = "unknown"


class RequiredCheckStatus(StrEnum):
    REQUIRED = "required"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "unknown"


class MergeQueueStatus(StrEnum):
    ENABLED = "enabled"
    NOT_ENABLED = "not_enabled"
    UNKNOWN = "unknown"


class IntegrationStatus(StrEnum):
    CONNECTED = "connected"
    ACTION_REQUIRED = "action_required"
    DISCONNECTED = "disconnected"


class HealthCheckStatus(StrEnum):
    OK = "ok"
    ATTENTION = "attention"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# Shared fragments
# --------------------------------------------------------------------------- #
class OrganizationRef(_View):
    id: int
    login: str
    type: str


class RepositoryLink(_View):
    id: int
    installation_id: int
    full_name: str


class ActorView(_View):
    type: Literal["system", "github", "user"]
    id: int | None = None
    login: str | None = None


class PolicyEntry(_View):
    id: str
    enabled: bool
    action: Action


# --------------------------------------------------------------------------- #
# Scans and findings
# --------------------------------------------------------------------------- #
class ScanSummary(_View):
    id: str
    scan_id: str | None
    repository: RepositoryLink
    organization_id: int | None
    event: str
    pull_request_number: int | None
    ref: str | None
    base_sha: str | None
    head_sha: str
    check_name: str
    result: ScanResultStatus
    commits_scanned: int | None
    violations: int | None
    warnings: int | None
    findings: int | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    requested_by: str | None
    trigger: str
    execution: int
    failure_source: Literal["pull_request", "push", "merge_queue"]


class MatchView(_View):
    kind: str
    value: str
    rule: str


class EvidenceView(_View):
    source: str
    source_label: str
    value: str
    line_number: int | None
    matched: tuple[MatchView, ...]
    notes: tuple[str, ...]


class FindingView(_View):
    id: int
    violation_id: str | None
    rule_id: str
    detector: str
    title: str
    message: str
    severity: Severity
    confidence: str
    action: Action
    reason: str
    commit_sha: str | None
    author: str | None
    committer: str | None
    evidence: tuple[EvidenceView, ...]
    remediation: str


class ScanFailure(_View):
    kind: str | None
    message: str


class ScanDetail(_View):
    scan: ScanSummary
    conclusion: str | None
    tool_version: str | None
    rules_version: str | None
    policy_version: str | None
    policy_source: str | None
    organization_policy_version: int | None
    effective_policies: tuple[PolicyEntry, ...]
    detector_failures: int | None
    notices: tuple[str, ...]
    failure: ScanFailure | None
    findings: tuple[FindingView, ...]
    can_rescan: bool
    rescan_blocked_reason: str | None
    executions: int
    latest_execution: str
    merge_group: "MergeGroupView | None" = None


class ExecutionView(_View):
    """One execution of a logical scan (same repository, commits and check)."""

    id: str
    execution: int
    trigger: str
    current: bool  # the newest execution: it determines the current status
    result: ScanResultStatus
    head_sha: str
    base_sha: str | None
    organization_policy_version: int | None
    policy_version: str | None
    rules_version: str | None
    tool_version: str | None
    conclusion: str | None
    requested_by: str | None
    failure: ScanFailure | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


class ExecutionHistory(_View):
    scan_id: str
    items: tuple[ExecutionView, ...]
    policy_changed: bool  # executions were evaluated under different policy versions
    rules_changed: bool


class ScanComparison(_View):
    scan_id: str
    previous_scan_id: str | None
    new: tuple[str, ...]  # finding fingerprints
    resolved: tuple[str, ...]
    unchanged: tuple[str, ...]
    new_findings: tuple[FindingView, ...]
    resolved_findings: tuple[FindingView, ...]


# --------------------------------------------------------------------------- #
# Violations
# --------------------------------------------------------------------------- #
class ViolationSummary(_View):
    id: str
    rule_id: str
    title: str
    severity: Severity
    action: Action
    repository: RepositoryLink
    organization_id: int | None
    commit_sha: str | None
    author: str | None
    status: ViolationStatus
    first_detected_at: datetime
    last_detected_at: datetime
    detections: int


class ExposureView(_View):
    kind: Literal["pull_request", "branch", "merge_group"]
    label: str
    active: bool
    opened_at: datetime
    closed_at: datetime | None
    closed_reason: str | None


class AcknowledgementView(_View):
    by: str | None
    at: datetime
    note: str | None


class DetectionView(_View):
    scan: str
    result: ScanResultStatus
    detected_at: datetime
    action: Action
    head_sha: str


class ViolationDetail(_View):
    violation: ViolationSummary
    detector: str
    message: str
    committer: str | None
    policy_reason: str
    evidence: tuple[EvidenceView, ...]
    remediation: str
    recommended_steps: tuple[str, ...]
    resolved_at: datetime | None
    resolution: str | None
    acknowledgement: AcknowledgementView | None
    exposures: tuple[ExposureView, ...]
    detections: tuple[DetectionView, ...]
    first_scan_id: str
    last_scan_id: str
    can_manage: bool


# --------------------------------------------------------------------------- #
# Repositories
# --------------------------------------------------------------------------- #
class EnforcementSignal(_View):
    status: str
    detail: str
    checked_at: datetime | None = None


class RequiredCheckSignal(_View):
    status: RequiredCheckStatus
    branch: str | None
    required_checks: tuple[str, ...]
    detail: str
    checked_at: datetime | None


class LatestCheckSignal(_View):
    result: ScanResultStatus | None
    scan: str | None
    head_sha: str | None
    completed_at: datetime | None


class EnforcementView(_View):
    github_app: EnforcementSignal
    github_actions: EnforcementSignal
    required_check: RequiredCheckSignal
    latest_check: LatestCheckSignal
    local_hooks: EnforcementSignal
    merge_queue: EnforcementSignal
    monitoring_enabled: bool


class RepositorySummary(_View):
    id: int
    installation_id: int
    organization: OrganizationRef | None
    owner: str
    name: str
    full_name: str
    github_url: str
    default_branch: str | None
    protection: ProtectionStatus
    protection_reason: str
    app_connection: AppConnection
    monitoring_enabled: bool
    last_scan: ScanSummary | None
    open_violations: int
    open_warnings: int
    critical_open: int


class RepositoryPermissions(_View):
    manage: bool
    trigger_scans: bool
    read_audit: bool


class RepositoryDetail(_View):
    repository: RepositorySummary
    enforcement: EnforcementView
    organization_policy_version: int | None
    effective_policies: tuple[PolicyEntry, ...]
    effective_policy_scan: str | None
    recent_scans: tuple[ScanSummary, ...]
    open_violations: tuple[ViolationSummary, ...]
    audit: tuple["AuditEventView", ...]
    permissions: RepositoryPermissions


class MergeGroupView(_View):
    head_sha: str
    base_sha: str
    base_ref: str
    pull_requests: tuple[int, ...]
    state: Literal["checks_requested", "destroyed"]
    destroyed_reason: str | None
    result: ScanResultStatus | None
    scan: str | None
    created_at: datetime
    updated_at: datetime
    validated_at: datetime | None


class MergeQueueView(_View):
    repository_id: int
    status: MergeQueueStatus
    detail: str
    checked_at: datetime | None
    permission: Literal["granted", "missing"]
    current: MergeGroupView | None
    recent: tuple[MergeGroupView, ...]


# --------------------------------------------------------------------------- #
# Policies and rules
# --------------------------------------------------------------------------- #
class PolicyRuleView(_View):
    policy_id: str
    name: str
    description: str
    default_action: Action
    service_floor: Action | None  # operator's mandatory policy file
    organization_floor: Action | None
    minimum_action: Action | None  # strongest floor; None = repository decides
    repository_override: Literal["any", "stricter_only"]
    source: Literal["built_in_default", "service_policy", "organization_policy"]


class PolicyAuthor(_View):
    id: int | None
    login: str | None


class OrganizationPolicyView(_View):
    organization: OrganizationRef
    version: int
    fingerprint: str | None
    updated_at: datetime | None
    updated_by: PolicyAuthor | None
    reason: str | None
    service_policy: str | None
    rules: tuple[PolicyRuleView, ...]
    can_write: bool


class PolicyChange(_View):
    policy_id: str
    old: Action | None
    new: Action | None
    weakening: bool


class PolicyVersionView(_View):
    version: int
    fingerprint: str
    floors: dict[str, Action]
    created_at: datetime
    created_by: PolicyAuthor
    reason: str | None
    status: Literal["active", "archived"]
    kind: Literal["change", "rollback"]
    rollback_of: int | None  # rollback: the version that was active before
    restored_version: int | None  # rollback: the version whose document was restored
    changes: tuple[PolicyChange, ...]  # compared with the previous version
    summary: str


class PolicyDiffEntry(_View):
    policy_id: str
    old: Action | None = None
    new: Action | None = None
    weakening: bool = False


class PolicyDiffView(_View):
    from_version: int
    to_version: int
    added: tuple[PolicyDiffEntry, ...]
    changed: tuple[PolicyDiffEntry, ...]
    removed: tuple[PolicyDiffEntry, ...]
    weakening: bool


class RuleView(_View):
    id: str
    name: str
    description: str
    detector: str
    severity: Severity
    default_action: Action
    source: Literal["bundled"]
    trusted: bool
    status: Literal["active"]
    rules_version: str
    tool_version: str


class RuleDataFile(_View):
    name: str
    entries: int


class RuleDetail(_View):
    rule: RuleView
    remediation: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    data_files: tuple[RuleDataFile, ...]
    editable: bool


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditEventView(_View):
    id: str
    type: str
    occurred_at: datetime
    actor: ActorView
    organization_id: int | None
    installation_id: int | None
    repository: RepositoryLink | None
    head_sha: str | None
    action: Action | None
    summary: str
    data: dict[str, str | int | bool | None]
    scan: str | None


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
class InstallationView(_View):
    id: int
    account: OrganizationRef
    status: AppConnection
    repository_selection: str
    repositories: int
    permissions: dict[str, str]
    missing_permissions: tuple[str, ...]
    excessive_permissions: tuple[str, ...]
    installed_at: datetime
    updated_at: datetime
    last_event_at: datetime | None
    github_settings_url: str
    can_manage: bool


class InstallationRepositoryView(_View):
    id: int
    full_name: str
    connected: bool
    monitoring_enabled: bool
    added_at: datetime | None
    removed_at: datetime | None


class InstallationDetail(_View):
    installation: InstallationView
    recent_events: tuple[AuditEventView, ...]


class SyncResult(_View):
    installation_id: int
    repositories: int
    added: tuple[str, ...]
    removed: tuple[str, ...]
    synced_at: datetime


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
class OverviewPeriod(_View):
    key: Literal["24h", "7d", "30d"]
    start: datetime
    end: datetime


class OverviewSummary(_View):
    repositories_monitored: int
    repositories_protected: int
    repositories_at_risk: int
    repositories_unprotected: int
    repositories_unknown: int
    repositories_configuration_error: int
    scans: int
    scans_passed: int
    scans_blocked: int
    scans_error: int
    open_violations: int
    open_warnings: int
    critical_open: int
    high_open: int


class IntegrationView(_View):
    status: IntegrationStatus
    installations_connected: int
    installations_suspended: int
    installations_disconnected: int
    detail: str


class HealthCheck(_View):
    id: str
    label: str
    status: HealthCheckStatus
    detail: str


class OverviewView(_View):
    period: OverviewPeriod
    summary: OverviewSummary
    integration: IntegrationView
    health: tuple[HealthCheck, ...]
    recent_scans: tuple[ScanSummary, ...]
    recent_violations: tuple[ViolationSummary, ...]
    repository_health: tuple[RepositorySummary, ...]


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
class NotificationView(_View):
    id: str
    type: str
    category: str
    severity: Severity
    state: Literal["unread", "read", "archived"]
    title: str
    body: str
    organization_id: int
    repository: RepositoryLink | None
    resource_type: str
    resource_id: str
    link: str | None
    occurrences: int
    created_at: datetime
    last_occurred_at: datetime
    read_at: datetime | None


class NotificationCounts(_View):
    unread: int  # at most 1000
    unread_critical: int
    capped: bool  # more unread notifications than counted


class ChannelPreferenceView(_View):
    in_app: bool
    email: bool
    webhook: bool


class TypePreferenceView(_View):
    type: str
    label: str
    description: str
    category: str
    mandatory_in_app: bool
    organization: ChannelPreferenceView
    personal_in_app: bool  # the member's own inbox (always true when mandatory)
    receives_in_app: bool  # the member's role receives this type at all


class NotificationChannelsView(_View):
    in_app: bool
    email: bool
    webhook: bool
    mode: Literal["off", "deliver", "test"]


class WebhookEndpointView(_View):
    id: str
    url: str
    created_at: datetime
    created_by: str | None


class NotificationDeliveryView(_View):
    id: str
    notification_type: str
    title: str
    channel: str
    destination: str
    status: Literal["pending", "sent", "failed", "cancelled"]
    attempts: int
    failure_code: str | None
    last_attempt_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime


class OrganizationNotificationSettingsView(_View):
    organization: OrganizationRef
    version: int
    updated_at: datetime | None
    updated_by: str | None
    channels: NotificationChannelsView
    types: tuple[TypePreferenceView, ...]
    email_recipients: tuple[str, ...]  # only for notifications:manage
    webhooks: tuple[WebhookEndpointView, ...]  # only for notifications:manage
    can_manage: bool


class CreatedWebhookView(_View):
    endpoint: WebhookEndpointView
    signing_secret: str  # shown once; CommitGuard does not store it


# --------------------------------------------------------------------------- #
# Accounts, members and sessions
# --------------------------------------------------------------------------- #
class OrganizationView(_View):
    organization: OrganizationRef
    role: str
    implicit_role: bool
    permissions: tuple[str, ...]
    installation_ids: tuple[int, ...]


class MemberView(_View):
    user_id: int
    login: str | None
    role: str
    granted_by: str
    created_at: datetime
    updated_at: datetime
    implicit: bool


class SessionView(_View):
    id: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str
    current: bool


class UserView(_View):
    id: int
    login: str


class SessionInfo(_View):
    user: UserView
    session: SessionView
    csrf_token: str
    organizations: tuple[OrganizationView, ...]
    reauthentication_required_after: datetime


RepositoryDetail.model_rebuild()
ScanDetail.model_rebuild()
