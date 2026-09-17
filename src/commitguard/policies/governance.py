"""Governed policy resolution: which policy applies to a repository, and why.

The organization layer decides *what configuration applies*; the policy engine
(:mod:`commitguard.policies.evaluator`) still decides *what a finding means*.
This module is the pure part of that split: given the governance inputs for
one repository (policy layers, approved exceptions, onboarding mode) and,
at scan time, the repository's own ``.commitguard.yaml``, it builds the
effective :class:`~commitguard.policies.model.PolicySet` together with the
provenance of every rule. It performs no I/O and evaluates no findings.

Precedence, per rule (later steps win, subject to their own limits)::

    1. built-in default                     rules/policies/defaults.py
    2. organization policy  - default       replaced by a narrower default below
    3. repository group     - default       several groups: the most restrictive
    4. repository policy    - default       (set in the dashboard for one repository)
    5. repository configuration             .commitguard.yaml at the trusted revision
    6. mandatory requirements (floor)       the most restrictive mandatory entry of the
                                            service, organization, group and repository
                                            policies; nothing above can weaken it
    7. approved exception                   the only way below a floor; scoped, expiring,
                                            the most specific scope applies
    8. monitor mode                         block is reported as warn (report only)

Two strengths exist. A **mandatory** entry (``warn`` or ``block``) is a floor:
lower layers may make the rule stricter but never weaker, and a lower layer
that asks for less is recorded as a :class:`PolicyConflict` - never silently
dropped. A **default** entry is a baseline that narrower layers and the
repository configuration may replace in either direction. A rule no layer
mentions is decided by the repository configuration (or the built-in default).

Exceptions never add or remove detection: a finding under an exception is still
recorded, with the lowered action and the exception that lowered it.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from commitguard.config.schema import CommitGuardConfig, PolicyOverride
from commitguard.core.decision import Action
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.loader import build_policy_set
from commitguard.policies.mandatory import apply_mandatory_policies
from commitguard.policies.model import Policy, PolicySet
from commitguard.security.hashing import fingerprint, sha256_hex

_STRICT = ConfigDict(frozen=True, extra="forbid")

#: Limits on governance inputs (resource exhaustion protection).
MAX_LAYERS = 64
MAX_EXCEPTIONS = 256
MAX_LABEL_CHARS = 200


class Enforcement(StrEnum):
    """How strongly a policy layer requires an action."""

    MANDATORY = "mandatory"  # a floor: nothing below may weaken it
    DEFAULT = "default"  # a baseline: narrower layers may replace it


class PolicyLevel(StrEnum):
    BUILT_IN = "built_in"
    SERVICE = "service"
    ORGANIZATION = "organization"
    GROUP = "group"
    REPOSITORY_POLICY = "repository_policy"
    REPOSITORY_CONFIGURATION = "repository_configuration"
    EXCEPTION = "exception"
    MONITOR_MODE = "monitor_mode"


#: Order used to break ties between floors of equal strength (broadest first).
_LEVEL_ORDER = {
    PolicyLevel.SERVICE: 0,
    PolicyLevel.ORGANIZATION: 1,
    PolicyLevel.GROUP: 2,
    PolicyLevel.REPOSITORY_POLICY: 3,
}
LAYER_LEVELS = frozenset(_LEVEL_ORDER)


class RepositoryMode(StrEnum):
    ENFORCE = "enforce"
    MONITOR = "monitor"


class ExceptionScope(StrEnum):
    ORGANIZATION = "organization"
    GROUP = "group"
    REPOSITORY = "repository"


_SCOPE_SPECIFICITY = {
    ExceptionScope.REPOSITORY: 2,
    ExceptionScope.GROUP: 1,
    ExceptionScope.ORGANIZATION: 0,
}


class RuleRequirement(BaseModel):
    """One layer's requirement for one rule."""

    model_config = _STRICT

    action: Action
    enforcement: Enforcement = Enforcement.MANDATORY

    @model_validator(mode="after")
    def _floor_actions(self) -> "RuleRequirement":
        if self.enforcement is Enforcement.MANDATORY and self.action is Action.ALLOW:
            raise ValueError("a mandatory requirement must be warn or block")
        return self


def validate_rule_ids(rules: Iterable[str]) -> None:
    unknown = sorted(set(rules) - set(DEFAULT_POLICIES))
    if unknown:
        raise ValueError(f"unknown policy id(s): {', '.join(unknown)}")


class PolicyLayer(BaseModel):
    """A published policy version that applies to the repository."""

    model_config = _STRICT

    level: PolicyLevel
    source_id: str = Field(default="", max_length=64)  # group ID or repository ID
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    version: int | None = Field(default=None, ge=1)
    rules: dict[str, RuleRequirement] = {}

    @field_validator("level")
    @classmethod
    def _layer_level(cls, value: PolicyLevel) -> PolicyLevel:
        if value not in LAYER_LEVELS:
            raise ValueError(f"{value.value} is not a policy layer")
        return value

    @field_validator("rules")
    @classmethod
    def _known(cls, value: dict[str, RuleRequirement]) -> dict[str, RuleRequirement]:
        validate_rule_ids(value)
        return value


class ExceptionGrant(BaseModel):
    """An approved, active exception as seen by the resolver."""

    model_config = _STRICT

    exception_id: str = Field(min_length=1, max_length=64)
    rule_id: str
    action: Action
    scope: ExceptionScope
    scope_label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    expires_at: datetime | None = None  # None only for a documented permanent exception

    @field_validator("rule_id")
    @classmethod
    def _known(cls, value: str) -> str:
        validate_rule_ids([value])
        return value

    @field_validator("action")
    @classmethod
    def _lowering(cls, value: Action) -> Action:
        if value is Action.BLOCK:
            raise ValueError("an exception lowers enforcement to warn or allow")
        return value


class GovernanceInputs(BaseModel):
    """Everything the organization layer decides for one repository."""

    model_config = _STRICT

    layers: tuple[PolicyLayer, ...] = ()
    exceptions: tuple[ExceptionGrant, ...] = ()
    mode: RepositoryMode = RepositoryMode.ENFORCE

    @field_validator("layers")
    @classmethod
    def _bounded_layers(cls, value: tuple[PolicyLayer, ...]) -> tuple[PolicyLayer, ...]:
        if len(value) > MAX_LAYERS:
            raise ValueError(f"more than {MAX_LAYERS} policy layers")
        return value

    @field_validator("exceptions")
    @classmethod
    def _bounded_exceptions(cls, value: tuple[ExceptionGrant, ...]) -> tuple[ExceptionGrant, ...]:
        if len(value) > MAX_EXCEPTIONS:
            raise ValueError(f"more than {MAX_EXCEPTIONS} exceptions")
        return value

    @property
    def empty(self) -> bool:
        return not self.layers and not self.exceptions and self.mode is RepositoryMode.ENFORCE

    def canonical(self) -> str:
        """Deterministic JSON of the inputs that affect enforcement (labels excluded)."""
        ordered = sorted(self.layers, key=lambda item: (_LEVEL_ORDER[item.level], item.source_id))
        document = {
            "layers": [
                {
                    "level": layer.level.value,
                    "source": layer.source_id,
                    "version": layer.version,
                    "rules": {
                        rule: [req.action.value, req.enforcement.value]
                        for rule, req in sorted(layer.rules.items())
                    },
                }
                for layer in ordered
            ],
            "exceptions": sorted(
                [g.exception_id, g.rule_id, g.action.value, g.scope.value] for g in self.exceptions
            ),
            "mode": self.mode.value,
        }
        return json.dumps(document, separators=(",", ":"), sort_keys=True)

    @property
    def fingerprint(self) -> str:
        return sha256_hex(self.canonical().encode("utf-8"))

    def describe(self) -> str:
        parts = [layer.label for layer in self.ordered_layers()]
        if self.exceptions:
            parts.append(f"{len(self.exceptions)} exception(s)")
        if self.mode is RepositoryMode.MONITOR:
            parts.append("monitor mode")
        return " + ".join(parts) if parts else "no organization governance"

    def ordered_layers(self) -> list[PolicyLayer]:
        return sorted(self.layers, key=lambda layer: (_LEVEL_ORDER[layer.level], layer.label))


class PolicyConflict(BaseModel):
    """A lower layer asked for less than a mandatory requirement allows."""

    model_config = _STRICT

    policy_id: str
    requested_action: Action
    requested_enabled: bool
    requested_by: PolicyLevel
    requested_label: str
    required_action: Action
    required_by: PolicyLevel
    required_label: str
    effective_action: Action
    reason: str


class RuleProvenance(BaseModel):
    """The effective policy for one rule and where every part of it came from."""

    model_config = _STRICT

    policy_id: str
    enabled: bool
    action: Action
    source: PolicyLevel
    source_label: str
    enforcement: Enforcement | None = None
    required_action: Action | None = None  # the floor, when a mandatory entry exists
    required_by: PolicyLevel | None = None
    required_label: str | None = None
    conflict: PolicyConflict | None = None
    exception_id: str | None = None
    exception_expires_at: datetime | None = None
    action_before_exception: Action | None = None
    monitor_mode: bool = False  # block reported as warn
    repository_configuration_known: bool = True


class EffectivePolicy(BaseModel):
    """The resolved policy set for one repository plus its provenance."""

    model_config = _STRICT

    policies: tuple[Policy, ...]
    rules: tuple[RuleProvenance, ...]
    inputs_fingerprint: str
    description: str
    mode: RepositoryMode

    def policy_set(self) -> PolicySet:
        return PolicySet({p.id: p for p in self.policies})

    @property
    def conflicts(self) -> tuple[PolicyConflict, ...]:
        return tuple(r.conflict for r in self.rules if r.conflict is not None)

    @property
    def fingerprint(self) -> str:
        """Fingerprint of the evaluated policies (same scheme as scan results)."""
        return policy_set_fingerprint(self.policy_set())


def policy_set_fingerprint(policies: Mapping[str, Policy]) -> str:
    parts: list[str] = []
    for policy in policies.values():
        parts += [policy.id, str(policy.enabled), policy.action.value]
    return fingerprint(parts)


def _level_label(level: PolicyLevel) -> str:
    return {
        PolicyLevel.BUILT_IN: "Built-in default",
        PolicyLevel.REPOSITORY_CONFIGURATION: "Repository configuration (.commitguard.yaml)",
    }.get(level, level.value.replace("_", " ").capitalize())


def _touched(configs: Sequence[CommitGuardConfig], policy_id: str) -> bool:
    return any(
        policy_id in config.policies and config.policies[policy_id].model_fields_set
        for config in configs
    )


def _effective_rank(policy: Policy) -> int:
    return policy.action.rank if policy.enabled else Action.ALLOW.rank


def resolve_policy(
    inputs: GovernanceInputs,
    repository_configs: Sequence[CommitGuardConfig] | None = None,
) -> EffectivePolicy:
    """Resolve the effective policy for one repository.

    ``repository_configs`` are the repository's configuration layers as loaded
    from the trusted revision (the built-in empty layer may be included). Pass
    ``None`` when they are not known (dashboard views between scans): the result
    then shows what governance guarantees, and marks the repository layer unknown.
    """
    layers = inputs.ordered_layers()
    known = repository_configs is not None
    configs = list(repository_configs or ())

    # Steps 2-4: defaults, narrower levels replacing broader ones.
    defaults: dict[str, tuple[Action, PolicyLevel, str]] = {}
    for level in (PolicyLevel.ORGANIZATION, PolicyLevel.GROUP, PolicyLevel.REPOSITORY_POLICY):
        level_entries: dict[str, list[tuple[Action, str]]] = {}
        for layer in (candidate for candidate in layers if candidate.level is level):
            for rule, requirement in layer.rules.items():
                if requirement.enforcement is Enforcement.DEFAULT:
                    level_entries.setdefault(rule, []).append((requirement.action, layer.label))
        for rule, entries in level_entries.items():
            # Several groups: the most restrictive default wins (ties: first label).
            action, label = max(entries, key=lambda entry: entry[0].rank)
            defaults[rule] = (action, level, label)
    defaults_config = CommitGuardConfig(
        version=1,
        policies={rule: PolicyOverride(action=entry[0]) for rule, entry in defaults.items()},
    )

    # Step 5: the repository configuration overrides defaults (policy engine layering).
    requested = build_policy_set(defaults_config, *configs)

    # Step 6: mandatory floors.
    floors: dict[str, tuple[Action, PolicyLevel, str]] = {}
    for layer in layers:
        for rule, requirement in layer.rules.items():
            if requirement.enforcement is not Enforcement.MANDATORY:
                continue
            current = floors.get(rule)
            if current is None or requirement.action.rank > current[0].rank:
                floors[rule] = (requirement.action, layer.level, layer.label)
    floor_config = CommitGuardConfig(
        version=1,
        policies={rule: PolicyOverride(action=entry[0]) for rule, entry in floors.items()},
    )
    floored = apply_mandatory_policies(requested, floor_config) if floors else requested

    grants: dict[str, list[ExceptionGrant]] = {}
    for grant in inputs.exceptions:
        grants.setdefault(grant.rule_id, []).append(grant)

    policies: list[Policy] = []
    provenance: list[RuleProvenance] = []
    for policy_id in sorted(DEFAULT_POLICIES):
        before_floor = requested[policy_id]
        if _touched(configs, policy_id):
            source, label, enforcement = (
                PolicyLevel.REPOSITORY_CONFIGURATION,
                _level_label(PolicyLevel.REPOSITORY_CONFIGURATION),
                None,
            )
        elif policy_id in defaults:
            _, source, label = defaults[policy_id]
            enforcement = Enforcement.DEFAULT
        else:
            source, label, enforcement = (
                PolicyLevel.BUILT_IN,
                _level_label(PolicyLevel.BUILT_IN),
                None,
            )
        policy = floored[policy_id]
        conflict = None
        floor = floors.get(policy_id)
        if floor is not None:
            floor_action, floor_level, floor_label = floor
            if source is PolicyLevel.BUILT_IN:
                # Nobody asked for less: the floor simply decides the rule.
                if policy.action.rank == floor_action.rank:
                    source, label, enforcement = floor_level, floor_label, Enforcement.MANDATORY
            elif _effective_rank(before_floor) < floor_action.rank:
                asked = before_floor.action if before_floor.enabled else Action.ALLOW
                conflict = PolicyConflict(
                    policy_id=policy_id,
                    requested_action=asked,
                    requested_enabled=before_floor.enabled,
                    requested_by=source,
                    requested_label=label,
                    required_action=floor_action,
                    required_by=floor_level,
                    required_label=floor_label,
                    effective_action=policy.action,
                    reason=(
                        f"{floor_label} requires at least {floor_action.value} and is mandatory: "
                        f"{label} cannot weaken it."
                    ),
                )
                source, label, enforcement = floor_level, floor_label, Enforcement.MANDATORY

        exception_id = None
        exception_expires = None
        before_exception = None
        applicable = grants.get(policy_id, [])
        if applicable:
            specificity = max(_SCOPE_SPECIFICITY[g.scope] for g in applicable)
            narrowest = [g for g in applicable if _SCOPE_SPECIFICITY[g.scope] == specificity]
            # The most restrictive of the most specific exceptions (safer on overlap).
            grant = max(narrowest, key=lambda g: (g.action.rank, g.exception_id))
            if _effective_rank(policy) > grant.action.rank:
                before_exception = policy.action
                policy = policy.model_copy(update={"enabled": True, "action": grant.action})
                exception_id, exception_expires = grant.exception_id, grant.expires_at
                source, label = PolicyLevel.EXCEPTION, f"Exception ({grant.scope_label})"

        monitor = False
        if (
            inputs.mode is RepositoryMode.MONITOR
            and policy.enabled
            and policy.action is Action.BLOCK
        ):
            policy = policy.model_copy(update={"action": Action.WARN})
            monitor = True

        policies.append(policy)
        provenance.append(
            RuleProvenance(
                policy_id=policy_id,
                enabled=policy.enabled,
                action=policy.action,
                source=source,
                source_label=label,
                enforcement=enforcement,
                required_action=floor[0] if floor else None,
                required_by=floor[1] if floor else None,
                required_label=floor[2] if floor else None,
                conflict=conflict,
                exception_id=exception_id,
                exception_expires_at=exception_expires,
                action_before_exception=before_exception,
                monitor_mode=monitor,
                repository_configuration_known=known,
            )
        )
    return EffectivePolicy(
        policies=tuple(policies),
        rules=tuple(provenance),
        inputs_fingerprint=inputs.fingerprint,
        description=inputs.describe(),
        mode=inputs.mode,
    )


def floor_config(inputs: GovernanceInputs) -> CommitGuardConfig | None:
    """The combined mandatory floor of ``inputs`` (for display and compatibility)."""
    floors: dict[str, Action] = {}
    for layer in inputs.layers:
        for rule, requirement in layer.rules.items():
            if requirement.enforcement is Enforcement.MANDATORY:
                floors[rule] = Action.most_restrictive(
                    [requirement.action, floors.get(rule, Action.WARN)]
                )
    if not floors:
        return None
    return CommitGuardConfig(
        version=1, policies={rule: PolicyOverride(action=action) for rule, action in floors.items()}
    )
