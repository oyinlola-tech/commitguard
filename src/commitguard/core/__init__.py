"""Core domain: scan context, findings, decisions and the detection engine.

The core knows nothing about the CLI, hooks, or how commits were obtained. It
receives a :class:`~commitguard.core.context.CommitContext`, runs registered
detectors, and returns a :class:`~commitguard.core.result.DetectionResult`.
"""
