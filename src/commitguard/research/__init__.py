"""Measurement and evidence: reproducible benchmarks of CommitGuard itself.

This package measures the product; it never changes a security decision. Every
benchmark drives the same code paths production uses - the
:class:`~commitguard.services.analysis.Analyzer` (detection engine + policy
evaluator), real ``git`` processes and the installed Git hooks - and records the
environment it ran in (:mod:`commitguard.research.environment`) so results can
be compared across versions and machines.

Modules::

    environment   benchmark manifest: versions, platform, CPU, RAM, fingerprints
    datasets      the labelled commit dataset (clean, violations, variations,
                  malformed, adversarial, generated) and its fingerprint
    metrics       confusion matrix, precision/recall/F1, latency percentiles
    detection     detection accuracy against the labelled dataset
    performance   detection latency, message-size scaling, CPU and memory
    hooks         Git operations with and without CommitGuard hooks
    repository    history-size scaling of range scans on real repositories
    platform      a portable self-check of the local enforcement path
    results       immutable result files and the run index
    report        security and benchmark reports from recorded results

Nothing here imports the GitHub App, the API or the dashboard, and nothing
contacts the network.
"""
