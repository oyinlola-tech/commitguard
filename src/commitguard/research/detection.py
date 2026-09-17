"""Detection accuracy on the labelled dataset.

Each case runs through the production :class:`~commitguard.services.analysis.Analyzer`
with the built-in rules and the built-in policy - the configuration a new
installation enforces. Three views are measured:

* **decision (binary)** - positive = the case must not be allowed silently
  (expected WARN or BLOCK); predicted positive = CommitGuard did not ALLOW.
  This is the security-relevant question: was a violation let through?
* **decision (exact)** - the reached decision equals the expected one
  (BLOCK vs WARN matters: a warning does not stop a merge);
* **rules** - per rule, whether it was reported when required, and whether a
  rule was reported that is neither required nor permitted.

Every mismatch is listed with the case, what was expected and what happened.
"""

import time
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.git.commit import Commit
from commitguard.policies.defaults import DEFAULT_POLICIES, default_policy_set
from commitguard.provenance.author import Identity
from commitguard.research.datasets import DatasetCase
from commitguard.research.metrics import Confusion, LatencySummary
from commitguard.services.analysis import Analyzer
from commitguard.services.reports import CommitReport

BENCHMARK_VERSION = "1.0.0"


class CaseOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    case_class: str
    category: str
    description: str
    expected_decision: str
    decision: str
    required_rules: tuple[str, ...]
    reported_rules: tuple[str, ...]
    missing_rules: tuple[str, ...]
    unexpected_rules: tuple[str, ...]
    failed_closed: bool
    outcome: str  # true_positive | false_positive | true_negative | false_negative


class DetectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark: str = "detection"
    benchmark_version: str = BENCHMARK_VERSION
    cases: int
    positives: int
    negatives: int
    decision: Confusion
    decision_rates: dict[str, float | None]
    exact_decision_matches: int
    exact_decision_accuracy: float | None
    rules: dict[str, Confusion]
    rule_rates: dict[str, dict[str, float | None]]
    by_class: dict[str, dict[str, int]]
    by_decision: dict[str, dict[str, int]]
    latency: LatencySummary
    duration_ms: float
    mismatches: tuple[CaseOutcome, ...]


def commit_for(case: DatasetCase) -> Commit:
    return Commit(
        author=Identity(name=case.author.name, email=case.author.email),
        committer=Identity(name=case.committer.name, email=case.committer.email),
        message=case.message,
    )


def _outcome(case: DatasetCase, report: CommitReport) -> CaseOutcome:
    reported = tuple(sorted({f.finding.rule_id for f in report.findings}))
    required = set(case.required_rules)
    allowed = required | set(case.permitted_rules)
    predicted_positive = report.action is not Action.ALLOW
    if case.positive:
        kind = "true_positive" if predicted_positive else "false_negative"
    else:
        kind = "false_positive" if predicted_positive else "true_negative"
    return CaseOutcome(
        id=case.id,
        case_class=case.case_class,
        category=case.category,
        description=case.description,
        expected_decision=case.expected_decision,
        decision=report.action.value,
        required_rules=case.required_rules,
        reported_rules=reported,
        missing_rules=tuple(sorted(required - set(reported))),
        unexpected_rules=tuple(sorted(set(reported) - allowed)),
        failed_closed=bool(report.failures),
        outcome=kind,
    )


def run_detection(
    cases: Sequence[DatasetCase], analyzer: Analyzer | None = None
) -> DetectionResult:
    if not cases:
        raise ValueError("the dataset is empty")
    analyzer = analyzer or Analyzer.create(default_policy_set())
    outcomes: list[CaseOutcome] = []
    durations: list[float] = []
    started = time.perf_counter()
    for case in cases:
        commit = commit_for(case)
        begin = time.perf_counter()
        report = analyzer.analyze(commit)
        durations.append(time.perf_counter() - begin)
        outcomes.append(_outcome(case, report))
    total = time.perf_counter() - started

    counts = Counter(o.outcome for o in outcomes)
    decision = Confusion(
        true_positive=counts["true_positive"],
        false_positive=counts["false_positive"],
        true_negative=counts["true_negative"],
        false_negative=counts["false_negative"],
    )
    rules: dict[str, Confusion] = {}
    for rule in sorted(DEFAULT_POLICIES):
        tp = fp = tn = fn = 0
        for case, outcome in zip(cases, outcomes, strict=True):
            expected = rule in case.required_rules
            permitted = rule in case.permitted_rules
            reported = rule in outcome.reported_rules
            if expected:
                tp, fn = (tp + 1, fn) if reported else (tp, fn + 1)
            elif permitted:
                continue  # either answer is acceptable for this case
            elif reported:
                fp += 1
            else:
                tn += 1
        rules[rule] = Confusion(
            true_positive=tp, false_positive=fp, true_negative=tn, false_negative=fn
        )

    by_class: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        bucket = by_class.setdefault(outcome.case_class, Counter())
        bucket["cases"] += 1
        bucket[outcome.outcome] += 1
        if outcome.decision == outcome.expected_decision:
            bucket["exact_decision"] += 1
    by_decision: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        bucket = by_decision.setdefault(outcome.expected_decision, Counter())
        bucket[outcome.decision] += 1

    exact = sum(1 for o in outcomes if o.decision == o.expected_decision)
    mismatches = tuple(
        o
        for o in outcomes
        if o.decision != o.expected_decision or o.missing_rules or o.unexpected_rules
    )
    return DetectionResult(
        cases=len(cases),
        positives=sum(1 for c in cases if c.positive),
        negatives=sum(1 for c in cases if not c.positive),
        decision=decision,
        decision_rates=decision.rates(),
        exact_decision_matches=exact,
        exact_decision_accuracy=exact / len(cases),
        rules=rules,
        rule_rates={rule: confusion.rates() for rule, confusion in rules.items()},
        by_class={k: dict(v) for k, v in sorted(by_class.items())},
        by_decision={k: dict(v) for k, v in sorted(by_decision.items())},
        latency=LatencySummary.from_seconds(durations),
        duration_ms=round(total * 1000, 3),
        mismatches=mismatches,
    )
