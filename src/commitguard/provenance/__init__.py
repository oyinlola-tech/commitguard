"""Provenance layer: who (or what) contributed a commit, and how we know.

This layer models and (eventually) analyses the *claims* a commit makes about
its origin: author, committer, co-authors, trailers and signatures. It is kept
separate from detectors so that provenance analysis can grow (signature
verification, identity correlation) without detectors re-implementing parsing.

Nothing in this package performs I/O or talks to Git.
"""
