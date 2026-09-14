"""Committer provenance analysis.

The committer is the identity that *created the commit object*, which may
differ from the author (rebases, cherry-picks, patches applied by maintainers,
or web UIs such as GitHub's ``web-flow``). Automation frequently reveals itself
here rather than in the author field.

TODO(phase-5): analyse author/committer relationships, e.g.
    * committer is a known automation identity while author is a human;
    * committer identity differs from the signing key identity;
    * implausible author/committer timestamp gaps.
"""

from commitguard.provenance.author import Identity

__all__ = ["Identity"]
