"""Built-in policy defaults.

These are the secure defaults applied when a repository has no configuration,
and the base that repository configuration overrides. Every rule a built-in
detector can emit must appear here (enforced by tests); configuration may only
reference IDs listed here.
"""

from types import MappingProxyType

from commitguard.core.decision import Action
from commitguard.policies.model import Policy, PolicySet

_DEFAULTS: tuple[Policy, ...] = (
    Policy(
        id="ai_coauthor",
        action=Action.BLOCK,
        description="AI agent listed as a co-author (e.g. Co-authored-by trailer).",
    ),
    Policy(
        id="ai_identity",
        action=Action.BLOCK,
        description="AI agent recorded as the commit author or committer.",
    ),
    Policy(
        id="ai_trailer",
        action=Action.BLOCK,
        description="AI attribution in other trailers (e.g. Generated-by, Assisted-by).",
    ),
    Policy(
        id="malformed_trailer",
        action=Action.WARN,
        description="Trailer-like line that does not parse (possible evasion attempt).",
    ),
    Policy(
        id="bot_identity",
        action=Action.WARN,
        description="Automation or bot account as author, committer or co-author.",
    ),
)

DEFAULT_POLICIES = MappingProxyType({policy.id: policy for policy in _DEFAULTS})
KNOWN_POLICY_IDS = frozenset(DEFAULT_POLICIES)


def default_policy_set() -> PolicySet:
    """Return the built-in policy set."""
    return PolicySet(DEFAULT_POLICIES)
