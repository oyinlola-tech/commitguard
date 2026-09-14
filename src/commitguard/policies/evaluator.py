"""Policy evaluation: :class:`ScanResult` + :class:`PolicySet` -> :class:`Decision`.

Evaluation rules (all fail closed):

* finding for a rule with an enabled policy  -> that policy's action;
* finding for a rule whose policy is disabled -> ALLOW (recorded, not hidden);
* finding for a rule with **no** policy       -> BLOCK;
* detector failure                            -> BLOCK (an incomplete scan
  cannot prove the commit is clean);
* the overall decision is the most restrictive individual action.
"""

from commitguard.core.decision import Action, Decision, Explanation
from commitguard.core.result import ScanResult
from commitguard.policies.model import PolicySet


class PolicyEvaluator:
    """Apply a :class:`PolicySet` to scan results."""

    def __init__(self, policies: PolicySet) -> None:
        self._policies = policies

    def evaluate(self, result: ScanResult) -> Decision:
        explanations: list[Explanation] = []

        for finding in result.findings:
            policy = self._policies.get(finding.rule)
            if policy is None:
                explanations.append(
                    Explanation(
                        action=Action.BLOCK,
                        reason=f"no policy configured for rule {finding.rule!r}; failing closed",
                        finding=finding,
                    )
                )
            elif not policy.enabled:
                explanations.append(
                    Explanation(
                        action=Action.ALLOW,
                        reason=f"policy {policy.id!r} is disabled",
                        policy_id=policy.id,
                        finding=finding,
                    )
                )
            else:
                explanations.append(
                    Explanation(
                        action=policy.action,
                        reason=f"policy {policy.id!r} action is {policy.action.value}",
                        policy_id=policy.id,
                        finding=finding,
                    )
                )

        for failure in result.failures:
            explanations.append(
                Explanation(
                    action=Action.BLOCK,
                    reason=f"detector {failure.detector!r} did not complete; failing closed",
                    failure=failure,
                )
            )

        action = Action.most_restrictive([e.action for e in explanations])
        return Decision(action=action, explanations=tuple(explanations))
