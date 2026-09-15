"""Enforcement: translate a policy result into what an entry point does.

The policy engine decides ALLOW / WARN / BLOCK. This service turns that
decision - or the failure to reach one - into an :class:`EnforcementDecision`.
``commitguard ci github`` and the GitHub App use it directly; the Git hooks
(:mod:`commitguard.services.hooks`) follow the same exit-code contract:

=====================  ==============  ==================  ================
State                  CLI / Action    Git hook            GitHub Check
=====================  ==============  ==================  ================
passed                 exit 0          commit/push allowed ``success``
passed_with_warnings   exit 0          allowed             ``success`` (+ warnings)
blocked                exit 1          rejected            ``failure``
error                  exit 2          rejected            ``failure`` / ``timed_out``
=====================  ==============  ==================  ================

``blocked`` (the scan worked and found a violation) and ``error`` (the scan
could not be completed) are kept apart for audit and reporting even though
both fail closed.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.services.reports import ScanReport


class EnforcementState(StrEnum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    BLOCKED = "blocked"
    ERROR = "error"


class FailureKind(StrEnum):
    CONFIGURATION = "configuration"  # invalid trusted/mandatory policy or rules
    AUTHORIZATION = "authorization"  # the integration may not access the repository
    INFRASTRUCTURE = "infrastructure"  # Git, storage, GitHub API unavailable
    TIMEOUT = "timeout"
    INTERNAL = "internal"  # unexpected error


class EnforcementDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: EnforcementState
    action: Action | None = None
    failure: FailureKind | None = None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.state in (EnforcementState.PASSED, EnforcementState.PASSED_WITH_WARNINGS)

    @property
    def exit_code(self) -> int:
        """0 allowed, 1 blocked by policy, 2 could not be verified (stable CLI contract)."""
        return {
            EnforcementState.PASSED: 0,
            EnforcementState.PASSED_WITH_WARNINGS: 0,
            EnforcementState.BLOCKED: 1,
            EnforcementState.ERROR: 2,
        }[self.state]

    @property
    def check_conclusion(self) -> str:
        if self.allowed:
            return "success"
        if self.failure is FailureKind.TIMEOUT:
            return "timed_out"
        return "failure"


class EnforcementService:
    def __init__(self, fail_on: Action = Action.BLOCK) -> None:
        if fail_on is Action.ALLOW:
            raise ValueError("fail_on must be block or warn")
        self.fail_on = fail_on

    def decide(self, report: ScanReport) -> EnforcementDecision:
        action = report.action
        if action.rank >= self.fail_on.rank:
            return EnforcementDecision(
                state=EnforcementState.BLOCKED,
                action=action,
                reason=f"policy result {action.value} is at or above fail-on {self.fail_on.value}",
            )
        if action is Action.WARN:
            return EnforcementDecision(
                state=EnforcementState.PASSED_WITH_WARNINGS,
                action=action,
                reason="warnings only",
            )
        return EnforcementDecision(
            state=EnforcementState.PASSED, action=action, reason="all policies passed"
        )

    @staticmethod
    def failed(kind: FailureKind, reason: str) -> EnforcementDecision:
        return EnforcementDecision(state=EnforcementState.ERROR, failure=kind, reason=reason)
