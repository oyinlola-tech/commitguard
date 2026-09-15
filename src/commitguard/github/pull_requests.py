"""Which pull request webhook actions matter to CommitGuard.

=====================================  ==========================================
Action                                 What happens
=====================================  ==========================================
``opened``, ``synchronize``,           scan the complete PR commit set
``reopened``                           (``head ^base``, trusted base policy)
``edited`` with a base branch change   scan again (different base and policy)
``closed`` and merged                  record the final state (audit); no scan
``closed`` without merge               cancel queued scans; no scan
anything else (labels, reviews...)     ignored: cannot change the commit set
=====================================  ==========================================

A ``synchronize`` scan always evaluates ``base..new head``, never only the
newly pushed commits, so an authoritative result covers every commit the PR
would merge.
"""

from enum import StrEnum

from commitguard.github.events import PULL_REQUEST_SCAN_ACTIONS, PullRequestEvent


class PullRequestDisposition(StrEnum):
    SCAN = "scan"
    RECORD_MERGE = "record_merge"
    CLOSE = "close"
    IGNORE = "ignore"


def disposition(event: PullRequestEvent) -> PullRequestDisposition:
    if event.action in PULL_REQUEST_SCAN_ACTIONS:
        return PullRequestDisposition.SCAN
    if event.action == "edited" and event.base_changed:
        return PullRequestDisposition.SCAN
    if event.action == "closed":
        return PullRequestDisposition.RECORD_MERGE if event.merged else PullRequestDisposition.CLOSE
    return PullRequestDisposition.IGNORE


def group_key(number: int) -> str:
    return f"pull_request:{int(number)}"
