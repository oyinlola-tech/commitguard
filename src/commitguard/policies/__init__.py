"""Policy engine: decides what happens to findings.

Detection and policy are separate on purpose. A detector reports *that* a
commit lists an AI co-author; the repository's policy decides whether that is
allowed, merits a warning, or blocks the commit/push.
"""
