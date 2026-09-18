"""Configuration schema.

Every model forbids extra keys and uses strict scalar types, so that e.g.
``enabled: "no"`` or a misspelled ``acton: allow`` is an error instead of a
silent change in security behaviour.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from commitguard.core.decision import Action
from commitguard.policies.defaults import KNOWN_POLICY_IDS


class PolicyOverride(BaseModel):
    """Repository override for one built-in policy. Unset fields keep defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: StrictBool | None = None
    action: Action | None = None

    @field_validator("enabled", "action", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        # ``action:`` with no value in YAML is null; treat it as a mistake.
        if value is None:
            raise ValueError("must not be null; remove the key to use the default")
        return value


class EnforcementOverride(BaseModel):
    """Which Git hooks enforce policy. Unset fields keep the value from lower layers.

    Disabling a hook never changes *policies*; it only stops that hook from
    running the analysis, and ``commitguard doctor`` reports enforcement as
    incomplete.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pre_commit: StrictBool | None = None
    commit_msg: StrictBool | None = None
    pre_push: StrictBool | None = None
    max_push_commits: StrictInt | None = Field(default=None, ge=1, le=1_000_000)

    @field_validator("pre_commit", "commit_msg", "pre_push", "max_push_commits", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null; remove the key to use the default")
        return value


class RemediationOverride(BaseModel):
    """What CommitGuard may do about a violation, beyond reporting it.

    ``auto_remove`` lets the ``commit-msg`` hook delete the offending lines from
    the pending message instead of refusing the commit. It only ever applies
    when *every* blocking finding is a message line: attribution carried by the
    author or committer identity cannot be fixed by editing text, and still
    blocks. The stripped message is re-analysed, and the commit proceeds only if
    it is then clean.

    It is off by default, because it edits what the developer wrote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_remove: StrictBool | None = None

    @field_validator("auto_remove", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("must not be null; remove the key to use the default")
        return value


class CommitGuardConfig(BaseModel):
    """Top-level ``.commitguard.yaml`` document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    policies: dict[str, PolicyOverride] = {}
    enforcement: EnforcementOverride = EnforcementOverride()
    remediation: RemediationOverride = RemediationOverride()

    @field_validator("version", mode="before")
    @classmethod
    def _strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be the integer 1")
        return value

    @field_validator("policies")
    @classmethod
    def _known_policies(cls, value: dict[str, PolicyOverride]) -> dict[str, PolicyOverride]:
        unknown = sorted(set(value) - KNOWN_POLICY_IDS)
        if unknown:
            known = ", ".join(sorted(KNOWN_POLICY_IDS))
            raise ValueError(f"unknown policy id(s): {', '.join(unknown)} (known: {known})")
        return value
