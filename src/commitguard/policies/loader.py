"""Build the effective :class:`PolicySet` from validated configuration.

Merge semantics: start from :data:`DEFAULT_POLICIES`; each policy named in the
configuration overrides only the fields it sets. A policy that is *omitted*
keeps its secure default - weakening enforcement always requires an explicit
entry in the configuration file.
"""

from commitguard.config.schema import CommitGuardConfig
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.model import Policy, PolicySet


def build_policy_set(config: CommitGuardConfig) -> PolicySet:
    """Return the effective policies for ``config``."""
    effective: dict[str, Policy] = dict(DEFAULT_POLICIES)
    for policy_id, override in config.policies.items():
        # Schema validation guarantees policy_id is a known ID.
        updates = override.model_dump(exclude_unset=True)
        effective[policy_id] = effective[policy_id].model_copy(update=updates)
    return PolicySet(effective)
