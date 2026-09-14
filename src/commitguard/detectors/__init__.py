"""Detectors: independent, pure analyses that turn a commit into findings.

A detector answers "does this commit match rule X, and what is the evidence?".
It never decides whether that is acceptable (see :mod:`commitguard.policies`)
and never performs I/O or modifies the repository.
"""
