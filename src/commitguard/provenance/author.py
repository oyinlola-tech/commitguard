"""Contributor identities.

A :class:`Identity` is a *claimed* ``name <email>`` pair. Git performs no
verification of these values, so they must be treated as untrusted evidence,
never as proof of who wrote a change.
"""

from pydantic import BaseModel, ConfigDict


class Identity(BaseModel):
    """A claimed Git identity (author, committer, or co-author)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    email: str

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"


def parse_identity(value: str) -> Identity:
    """Parse a ``Name <email>`` string (e.g. from a ``Co-authored-by`` trailer).

    TODO(phase-2): implement a strict, well-tested parser. It must handle
    missing brackets, extra whitespace, multiple ``<``/``>`` characters,
    Unicode look-alikes and control characters without guessing silently; an
    unparseable value should be reported as a malformed trailer finding.
    """
    raise NotImplementedError("identity parsing is planned for Phase 2")
