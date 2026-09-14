"""Policy models."""

from collections.abc import Iterator, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from commitguard.core.decision import Action
from commitguard.security.validation import validate_identifier


class Policy(BaseModel):
    """The effective policy for a single rule ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    enabled: bool = True
    action: Action
    description: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return validate_identifier(value, kind="policy id")


class PolicySet(Mapping[str, Policy]):
    """An immutable mapping of rule ID to effective :class:`Policy`."""

    def __init__(self, policies: Mapping[str, Policy]) -> None:
        for key, policy in policies.items():
            if key != policy.id:
                raise ValueError(f"policy key {key!r} does not match policy id {policy.id!r}")
        self._policies = dict(sorted(policies.items()))

    def __getitem__(self, key: str) -> Policy:
        return self._policies[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._policies)

    def __len__(self) -> int:
        return len(self._policies)

    def __repr__(self) -> str:
        return f"PolicySet({list(self._policies)})"
