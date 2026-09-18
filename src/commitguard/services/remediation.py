"""Removing prohibited attribution from a pending commit message.

Only the ``commit-msg`` hook can do this safely. It runs *before* Git creates
the commit object, so deleting a line rewrites nothing: there is no history to
rewrite yet. By the time ``pre-push`` runs, the commits exist and correcting
them means rewriting history, which CommitGuard never does on its own.

The rules are deliberately narrow, because this feature turns a block into a
commit:

* it applies only when **every** blocking finding points at a message line.
  Attribution carried by the author or committer identity cannot be fixed by
  editing text, so it still blocks;
* a detector failure always blocks - a message that could not be fully analysed
  is never "fixed";
* the stripped message is **re-analysed**, and the commit proceeds only if the
  result is clean. Nothing is ever allowed on the assumption that the removal
  worked;
* if nothing but blank lines would be left, the commit blocks instead, rather
  than producing an empty message.
"""

from pydantic import BaseModel, ConfigDict

from commitguard.core.decision import Action
from commitguard.core.result import EvidenceSource
from commitguard.services.reports import CommitReport

#: Evidence that lives on a line of the message, and so can be deleted.
REMOVABLE_SOURCES = frozenset(
    {EvidenceSource.COAUTHOR_TRAILER, EvidenceSource.TRAILER, EvidenceSource.MESSAGE}
)


class RemovedLine(BaseModel):
    """One line deleted from a pending commit message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line_number: int
    text: str
    rule_id: str


def removable_lines(report: CommitReport) -> tuple[RemovedLine, ...] | None:
    """The message lines whose deletion would clear every block, or ``None``.

    ``None`` means "editing the message cannot fix this": an identity-based
    finding, a detector failure, or evidence with no line number.
    """
    if any(failure.action is Action.BLOCK for failure in report.failures):
        return None
    removals: dict[int, RemovedLine] = {}
    for evaluated in report.findings:
        if evaluated.action is not Action.BLOCK:
            continue  # warnings do not block, so nothing has to be removed for them
        for evidence in evaluated.finding.evidence:
            if evidence.source not in REMOVABLE_SOURCES or evidence.line_number is None:
                return None
            removals.setdefault(
                evidence.line_number,
                RemovedLine(
                    line_number=evidence.line_number,
                    text=evidence.value,
                    rule_id=evaluated.finding.rule_id,
                ),
            )
    return tuple(removals[number] for number in sorted(removals)) or None


def with_actual_text(message: str, removals: tuple[RemovedLine, ...]) -> tuple[RemovedLine, ...]:
    """Replace each removal's evidence value with the message line itself.

    Evidence carries the matched value (``Claude <noreply@anthropic.com>``);
    the developer needs to see the whole line that disappeared.
    """
    lines = message.splitlines()
    return tuple(
        removed.model_copy(update={"text": lines[removed.line_number - 1].strip()})
        if 0 < removed.line_number <= len(lines)
        else removed
        for removed in removals
    )


def strip_lines(message: str, line_numbers: frozenset[int]) -> str:
    """Return ``message`` without the given 1-based lines.

    Blank lines left stranded at the end are trimmed, so deleting a trailing
    trailer block does not leave the message ending in blank lines.
    """
    kept = [
        line
        for number, line in enumerate(message.splitlines(), start=1)
        if number not in line_numbers
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept) + "\n" if kept else ""
