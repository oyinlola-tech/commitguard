"""Configuration schema.

Every model forbids extra keys and uses strict scalar types, so that e.g.
``enabled: "no"`` or a misspelled ``acton: allow`` is an error instead of a
silent change in security behaviour.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

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


class CommitGuardConfig(BaseModel):
    """Top-level ``.commitguard.yaml`` document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    policies: dict[str, PolicyOverride] = {}

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
