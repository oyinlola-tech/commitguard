"""Commit message trailers (``Key: value`` lines such as ``Co-authored-by``).

Trailers are the primary place AI agents record attribution, for example::

    feat: implement authentication

    Co-authored-by: Claude <noreply@anthropic.com>
"""

from pydantic import BaseModel, ConfigDict, Field

COAUTHOR_TRAILER_KEY = "co-authored-by"


class Trailer(BaseModel):
    """A single trailer parsed from a commit message.

    ``key`` preserves the original spelling; use :attr:`normalized_key` for
    comparisons, because Git trailer keys are case-insensitive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    value: str
    line_number: int = Field(ge=1, description="1-based line in the commit message")

    @property
    def normalized_key(self) -> str:
        return self.key.strip().lower()


def parse_trailers(message: str) -> tuple[Trailer, ...]:
    """Extract trailers from a raw commit message.

    TODO(phase-2): implement. Requirements already agreed:
      * follow Git's definition (final paragraph) but also record trailer-like
        lines elsewhere, since hosting providers are more permissive;
      * case-insensitive keys, tolerate surrounding whitespace;
      * never execute, unescape or evaluate content;
      * report, rather than drop, malformed trailer-like lines;
      * bounded work on pathological input (very long messages/lines).
    """
    raise NotImplementedError("trailer parsing is planned for Phase 2")
