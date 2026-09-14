"""Build the effective :class:`PolicySet` from validated configuration layers.

Merge semantics: start from :data:`DEFAULT_POLICIES`; each configuration layer
(in precedence order, lowest first) overrides only the policy fields it sets.
A policy that is *omitted* keeps its value from the layer below and,
ultimately, its secure default - weakening enforcement always requires an
explicit entry.
"""

from commitguard.config.schema import CommitGuardConfig
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.model import Policy, PolicySet


def build_policy_set(*configs: CommitGuardConfig) -> PolicySet:
    """Return the effective policies for configuration layers ``configs``."""
    effective: dict[str, Policy] = dict(DEFAULT_POLICIES)
    for config in configs:
        for policy_id, override in config.policies.items():
            # Schema validation guarantees policy_id is a known ID.
            updates = override.model_dump(exclude_unset=True)
            effective[policy_id] = effective[policy_id].model_copy(update=updates)
    return PolicySet(effective)
