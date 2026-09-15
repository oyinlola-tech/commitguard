"""Mandatory policies: a floor that repository configuration cannot weaken.

Ordinary configuration layers *override* each other (a repository may change
``bot_identity`` from warn to allow). A mandatory policy - set by the operator
of a central service such as the GitHub App, and in future by an organisation -
is applied *after* those layers and can only make enforcement stricter:

* a policy it names is always enabled;
* its action is the more restrictive of the mandatory action and the action
  the lower layers produced.

So with a mandatory ``ai_coauthor: block``, a repository ``ai_coauthor: allow``
(or ``enabled: false``) still results in BLOCK. A mandatory policy cannot
disable or relax anything; ``enabled: false`` is rejected as a configuration
error rather than silently ignored.
"""

from commitguard.config.schema import CommitGuardConfig
from commitguard.core.decision import Action
from commitguard.policies.defaults import DEFAULT_POLICIES
from commitguard.policies.model import PolicySet


def validate_mandatory_config(config: CommitGuardConfig) -> None:
    """Raise ``ValueError`` if ``config`` tries to weaken rather than enforce."""
    disabled = sorted(pid for pid, o in config.policies.items() if o.enabled is False)
    if disabled:
        raise ValueError(
            "a mandatory policy can only enforce policies; enabled: false is not allowed for "
            + ", ".join(disabled)
        )
    if config.enforcement.model_fields_set:
        raise ValueError("a mandatory policy cannot configure local hook enforcement")


def apply_mandatory_policies(policies: PolicySet, mandatory: CommitGuardConfig) -> PolicySet:
    """Return ``policies`` with the mandatory floor applied (never weaker)."""
    validate_mandatory_config(mandatory)
    effective = dict(policies)
    for policy_id, override in mandatory.policies.items():
        current = effective[policy_id]
        floor = override.action or DEFAULT_POLICIES[policy_id].action
        action = current.action if current.enabled else Action.ALLOW
        effective[policy_id] = current.model_copy(
            update={"enabled": True, "action": Action.most_restrictive([action, floor])}
        )
    return PolicySet(effective)
