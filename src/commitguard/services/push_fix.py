"""Removing prohibited attribution from commits at push time (``fix_on_push``).

``commit-msg`` cleans a message before the commit exists. Some commits never
pass through it: ``git commit --no-verify``, ``git cherry-pick``,
``git rebase``, ``git am``, and tools that create commits without running
hooks. Those reach ``pre-push`` still carrying the attribution.

A ``pre-push`` hook cannot change what the push in progress sends: Git has
already chosen the objects. So this rewrites the *unpushed* commits on the
branch and then stops the push; the next ``git push`` sends the corrected
commits.

This rewrites history, so every rule below errs towards doing nothing. Any
case that is not provably safe returns ``None`` and the push is blocked exactly
as it would have been without the setting:

* only commits that **no remote-tracking branch contains** are rewritten, so
  nothing anyone else may have fetched is ever changed;
* only **local branches** move, atomically, and only if each still points
  where it did when the push began; tags are never touched;
* every blocking finding must be a **message line**: an AI author or committer
  identity cannot be fixed by editing text;
* commits carrying a signature (``gpgsig``, ``mergetag``) or any unexpected
  header are left alone, because rewriting would silently drop the signature;
* nothing happens during an unfinished rebase, merge, cherry-pick, revert or
  bisect;
* every rewritten commit is **re-analysed**, and nothing moves unless all of
  them come out clean.

Only the message changes. Tree, parents (remapped to their rewritten copies),
author, committer and both timestamps are copied byte for byte, so the working
tree and index are untouched and the old commits stay in the reflog.
"""

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from commitguard.core.context import ScanTrigger
from commitguard.core.decision import Action
from commitguard.git.commit import Commit
from commitguard.git.repository import Repository
from commitguard.services.analysis import Analyzer
from commitguard.services.remediation import (
    RemovedLine,
    removable_lines,
    strip_lines,
    with_actual_text,
)
from commitguard.services.reports import CommitReport

REFLOG_REASON = "commitguard: remove AI attribution before push"

#: Commit headers that can be copied unchanged when only the message changes.
_COPYABLE_HEADERS = (b"tree", b"parent", b"author", b"committer", b"encoding")


class RewrittenCommit(BaseModel):
    """One commit replaced by a copy with a corrected message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    old_sha: str
    new_sha: str
    subject: str
    #: Empty when the commit was rewritten only because an ancestor was.
    removed: tuple[RemovedLine, ...] = ()


class PushFix(BaseModel):
    """What ``fix_on_push`` changed. The push that triggered it is stopped."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    branches: tuple[str, ...]
    commits: tuple[RewrittenCommit, ...]


class BranchUpdate(BaseModel):
    """A branch the push would update, with its outgoing commits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_ref: str
    local_oid: str
    commits: tuple[str, ...]


def _topological(shas: set[str], commits: Mapping[str, Commit]) -> list[str] | None:
    """Parents before children, within ``shas``. ``None`` if that is impossible."""
    ordered: list[str] = []
    pending = set(shas)
    while pending:
        ready = sorted(
            sha for sha in pending if all(p not in pending for p in commits[sha].parents)
        )
        if not ready:
            return None  # a cycle cannot happen in Git; refuse rather than guess
        for sha in ready:
            ordered.append(sha)
            pending.discard(sha)
    return ordered


def _rewrite(raw: bytes, parents: Sequence[str], message: str) -> bytes | None:
    """The commit object with new parents and message, or ``None`` if unsafe."""
    headers, separator, _ = raw.partition(b"\n\n")
    if not separator:
        return None
    kept: list[bytes] = []
    for line in headers.split(b"\n"):
        name = line.split(b" ", 1)[0]
        if line.startswith(b" ") or name not in _COPYABLE_HEADERS:
            return None  # a signature, a mergetag, or something we do not understand
        if name == b"encoding" and line.split(b" ", 1)[-1].strip().lower() not in (
            b"utf-8",
            b"utf8",
        ):
            return None
        if name == b"tree":
            kept.append(line)
            kept.extend(b"parent " + p.encode("ascii") for p in parents)
        elif name != b"parent":
            kept.append(line)
    return b"\n".join(kept) + b"\n\n" + message.encode("utf-8")


def fix_outgoing_commits(
    repository: Repository,
    analyzer: Analyzer,
    updates: Sequence[BranchUpdate],
    commits: Mapping[str, Commit],
    reports: Mapping[str, CommitReport],
) -> PushFix | None:
    """Rewrite the blocked unpushed commits, or return ``None`` and change nothing."""
    blocked = {sha for sha, report in reports.items() if report.action is Action.BLOCK}
    if not blocked:
        return None
    removals: dict[str, tuple[RemovedLine, ...]] = {}
    for sha in blocked:
        lines = removable_lines(reports[sha])
        if lines is None:
            return None  # an identity finding or a detector failure: text cannot fix it
        removals[sha] = lines
    if repository.operation_in_progress() is not None:
        return None

    targets = [u for u in updates if blocked & set(u.commits)]
    if not targets or any(not u.local_ref.startswith("refs/heads/") for u in targets):
        return None  # a tag carries it, or nothing we are allowed to move
    if blocked - {sha for u in targets for sha in u.commits}:
        return None

    outgoing = {sha for u in targets for sha in u.commits}
    order = _topological(outgoing, commits)
    if order is None:
        return None

    mapping: dict[str, str] = {}
    rewritten: list[RewrittenCommit] = []
    for sha in order:
        commit = commits[sha]
        parents = [mapping.get(p, p) for p in commit.parents]
        if sha not in removals and parents == list(commit.parents):
            continue
        raw = repository.read_raw_commit(sha)
        try:
            message = raw.partition(b"\n\n")[2].decode("utf-8")
        except UnicodeDecodeError:
            return None
        lines = removals.get(sha, ())
        if lines:
            # Evidence line numbers refer to the analysed message; they must agree.
            if message.rstrip("\n") != commit.message.rstrip("\n"):
                return None
            new_message = strip_lines(message, frozenset(r.line_number for r in lines))
            if not new_message.strip():
                return None
        else:
            new_message = message
        data = _rewrite(raw, parents, new_message)
        if data is None:
            return None
        mapping[sha] = repository.write_commit_object(data)
        rewritten.append(
            RewrittenCommit(
                old_sha=sha,
                new_sha=mapping[sha],
                subject=commit.subject,
                removed=with_actual_text(message, lines) if lines else (),
            )
        )

    # Never change a commit that any remote already has.
    if repository.not_on_any_remote(list(mapping)) != set(mapping):
        return None
    for fixed in repository.read_commits(list(mapping.values())):
        if analyzer.analyze(fixed, ScanTrigger.PRE_PUSH).action is Action.BLOCK:
            return None  # the rewrite did not fix it: never move a branch on assumption

    moves = []
    for update in targets:
        new_tip = mapping.get(update.local_oid)
        if new_tip is None:
            return None
        moves.append((update.local_ref, new_tip, update.local_oid))
    repository.move_branches(moves, REFLOG_REASON)
    return PushFix(
        branches=tuple(u.local_ref for u in targets),
        commits=tuple(rewritten),
    )
