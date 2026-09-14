"""Application services shared by every entry point.

``commitguard scan``, ``commitguard check`` and (in Phase 3) the Git hooks all
run the same pipeline through this package, so no entry point re-implements
detection or policy logic::

    Repository --> Commit --> Analyzer(DetectionEngine + PolicyEvaluator)
               --> CommitReport --> ScanReport (text / JSON / future audit log)
"""
