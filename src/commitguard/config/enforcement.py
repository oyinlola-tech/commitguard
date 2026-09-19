"""Effective hook enforcement and remediation settings, merged across layers."""

from pydantic import BaseModel, ConfigDict

from commitguard.config.schema import CommitGuardConfig

DEFAULT_MAX_PUSH_COMMITS = 10_000


class Enforcement(BaseModel):
    """Resolved enforcement settings. Secure default: every hook enforces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pre_commit: bool = True
    commit_msg: bool = True
    pre_push: bool = True
    max_push_commits: int = DEFAULT_MAX_PUSH_COMMITS

    def enabled(self, hook: str) -> bool:
        return bool(getattr(self, hook.replace("-", "_")))

    @property
    def complete(self) -> bool:
        return self.pre_commit and self.commit_msg and self.pre_push


def build_enforcement(*configs: CommitGuardConfig) -> Enforcement:
    """Merge enforcement settings from configuration layers (lowest first)."""
    effective = Enforcement()
    for config in configs:
        updates = config.enforcement.model_dump(exclude_unset=True)
        if updates:
            effective = effective.model_copy(update=updates)
    return effective


class Remediation(BaseModel):
    """Resolved remediation settings. Secure default: change nothing, just block."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    auto_remove: bool = False
    fix_on_push: bool = False


def build_remediation(*configs: CommitGuardConfig) -> Remediation:
    """Merge remediation settings from configuration layers (lowest first)."""
    effective = Remediation()
    for config in configs:
        updates = config.remediation.model_dump(exclude_unset=True)
        if updates:
            effective = effective.model_copy(update=updates)
    return effective
