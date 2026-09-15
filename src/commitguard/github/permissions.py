"""GitHub App permissions: what CommitGuard needs, and nothing more.

=================  ======  =====================================================
Permission         Level   Why
=================  ======  =====================================================
``metadata``       read    mandatory for every App; repository lookup by ID
``contents``       read    fetch commit objects (no file contents are kept) and
                           receive ``push`` events
``pull_requests``  read    receive ``pull_request`` events; confirm the PR head
                           before publishing a result
``checks``         write   create and update the CommitGuard Check Run; receive
                           ``check_run`` / ``check_suite`` re-run requests
=================  ======  =====================================================

Optional (requested only for the feature that needs it):

=================  ======  =====================================================
``merge_queues``   read    receive ``merge_group`` events, so CommitGuard can
                           validate the exact commit a merge queue tests
=================  ======  =====================================================

An installation without an optional permission keeps working; the feature is
reported as unavailable. Optional permissions are never requested for
installation tokens: scanning a merge group needs only ``contents: read``.

CommitGuard never needs write access to contents, administration, actions,
workflows or members: it detects and reports, it never modifies a repository.
Installation tokens are additionally *down-scoped* to exactly the required
permissions and to the single repository being scanned.
"""

from collections.abc import Mapping
from types import MappingProxyType

REQUIRED_PERMISSIONS: Mapping[str, str] = MappingProxyType(
    {"checks": "write", "contents": "read", "metadata": "read", "pull_requests": "read"}
)
OPTIONAL_PERMISSIONS: Mapping[str, str] = MappingProxyType({"merge_queues": "read"})
WEBHOOK_EVENTS = ("installation", "installation_repositories", "pull_request", "push")
#: Subscriptions for Phase 7 features: GitHub "Re-run" buttons and merge queues.
OPTIONAL_WEBHOOK_EVENTS = ("check_run", "check_suite", "merge_group")

_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}


def level_rank(level: str | None) -> int:
    return _LEVELS.get(level or "none", 0)


def missing_permissions(
    granted: Mapping[str, str], required: Mapping[str, str] = REQUIRED_PERMISSIONS
) -> dict[str, str]:
    """Required permissions that ``granted`` does not satisfy (name -> required level)."""
    return {
        name: level
        for name, level in sorted(required.items())
        if level_rank(granted.get(name)) < level_rank(level)
    }


def excessive_permissions(
    granted: Mapping[str, str], required: Mapping[str, str] = REQUIRED_PERMISSIONS
) -> dict[str, str]:
    """Granted permissions above what CommitGuard needs or can use (name -> granted level)."""
    usable = {**OPTIONAL_PERMISSIONS, **required}
    return {
        name: level
        for name, level in sorted(granted.items())
        if level_rank(level) > level_rank(usable.get(name))
    }
