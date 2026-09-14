"""Security decisions.

A :class:`Decision` is the policy engine's verdict over a :class:`DetectionResult`,
with an explanation for every finding and failure that contributed to it.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from commitguard.core.result import DetectorFailure, Finding


class Action(StrEnum):
    """What to do about a finding. Ordered from least to most restrictive."""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"

    @property
    def rank(self) -> int:
        return _ACTION_RANK[self]

    @classmethod
    def most_restrictive(cls, actions: "list[Action]") -> "Action":
        return max(actions, key=lambda action: action.rank, default=cls.ALLOW)


_ACTION_RANK = {action: index for index, action in enumerate(Action)}


class Explanation(BaseModel):
    """Why a particular action was taken for one finding or failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    reason: str
    policy_id: str | None = None
    finding: Finding | None = None
    failure: DetectorFailure | None = None


class Decision(BaseModel):
    """Final verdict for one scan, with full explanations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Action
    explanations: tuple[Explanation, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.action is Action.BLOCK

    def explanations_for(self, action: Action) -> tuple[Explanation, ...]:
        return tuple(e for e in self.explanations if e.action is action)
