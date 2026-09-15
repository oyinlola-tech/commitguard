"""The analysis pipeline shared by ``scan``, ``check`` and future hooks/CI."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from commitguard import __version__
from commitguard.config.loader import LoadedConfig, load_effective_config
from commitguard.core.context import CommitContext, ScanTrigger
from commitguard.core.decision import Action
from commitguard.core.engine import DetectionEngine
from commitguard.detectors.registry import builtin_registry
from commitguard.git.commit import Commit
from commitguard.git.repository import Repository
from commitguard.policies.evaluator import PolicyEvaluator
from commitguard.policies.loader import build_policy_set
from commitguard.policies.model import PolicySet
from commitguard.provenance.author import Identity
from commitguard.rules.loader import load_builtin_rules
from commitguard.rules.matcher import CompiledRules
from commitguard.services.reports import CommitReport, ScanReport
from commitguard.utils.filesystem import read_bytes_limited

DEFAULT_MAX_COMMITS = 1000
MAX_MESSAGE_FILE_BYTES = 1024 * 1024


class Analyzer:
    """Detection engine + policy evaluator for a fixed rule set and policy set."""

    def __init__(self, engine: DetectionEngine, policies: PolicySet) -> None:
        self._engine = engine
        self._policies = policies
        self._evaluator = PolicyEvaluator(policies)
        self._enabled_rules = frozenset(p.id for p in policies.values() if p.enabled)

    @classmethod
    def create(cls, policies: PolicySet, rules: CompiledRules | None = None) -> "Analyzer":
        rules = rules if rules is not None else load_builtin_rules()
        return cls(DetectionEngine(builtin_registry(rules)), policies)

    @property
    def policies(self) -> PolicySet:
        return self._policies

    def analyze(self, commit: Commit, trigger: ScanTrigger = ScanTrigger.MANUAL) -> CommitReport:
        context = CommitContext(commit=commit, trigger=trigger)
        detection = self._engine.run(context, enabled_rules=self._enabled_rules)
        decision = self._evaluator.evaluate(detection)
        return CommitReport.build(commit.short_sha, detection, decision, subject=commit.subject)


def load_analyzer(
    repository: Repository | None, *, config_path: Path | None = None
) -> tuple[Analyzer, LoadedConfig]:
    """Load configuration layers and rules, and build an :class:`Analyzer`."""
    config = load_effective_config(
        repository.root if repository else None, explicit_path=config_path
    )
    return Analyzer.create(build_policy_set(*config.configs)), config


def build_report(
    reports: Sequence[CommitReport],
    *,
    repository: Repository | None,
    target: str,
    trigger: ScanTrigger,
    config: LoadedConfig,
) -> ScanReport:
    return ScanReport(
        tool_version=__version__,
        generated_at=datetime.now(UTC),
        repository=str(repository.root) if repository else None,
        target=target,
        trigger=trigger.value,
        config_sources=tuple(str(source) for source in config.sources),
        action=Action.most_restrictive([report.action for report in reports]),
        commits=tuple(reports),
    )


def analyze_revisions(
    repository: Repository,
    analyzer: Analyzer,
    revision_range: str,
    *,
    max_commits: int = DEFAULT_MAX_COMMITS,
    trigger: ScanTrigger = ScanTrigger.MANUAL,
) -> list[CommitReport]:
    """Analyse every commit selected by ``revision_range``."""
    shas = repository.list_commits(revision_range, max_count=max_commits)
    return [analyzer.analyze(commit, trigger) for commit in repository.read_commits(shas)]


def analyze_message_file(
    repository: Repository,
    analyzer: Analyzer,
    message_file: Path,
    *,
    trigger: ScanTrigger = ScanTrigger.CHECK,
) -> CommitReport:
    """Analyse a commit that does not exist yet (message file + configured identity).

    TODO(phase-3): apply Git's message cleanup (comment stripping) the way
    ``git commit`` does before the commit-msg hook result is used.
    """
    data = read_bytes_limited(message_file, max_bytes=MAX_MESSAGE_FILE_BYTES)
    author, committer = repository.pending_identities()
    return analyzer.analyze(
        pending_commit(data.decode("utf-8", "replace"), author, committer), trigger
    )


def pending_commit(message: str, author: Identity, committer: Identity) -> Commit:
    return Commit(author=author, committer=committer, message=message)
