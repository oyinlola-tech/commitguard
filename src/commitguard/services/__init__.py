"""Application services shared by every entry point.

* ``analysis``    - the Analyzer pipeline used by scan, check and hooks;
* ``ci``          - commit range and trusted policy planning for server-side checks;
* ``scan``        - ScanService: ScanRequest -> ScanResult (Action and GitHub App);
* ``enforcement`` - maps results to exit codes and check conclusions;
* ``audit``       - creates audit events with correlation IDs.

``commitguard scan``, ``commitguard check``, the Git hooks, ``ci github`` and the GitHub App all
run the same pipeline through this package, so no entry point re-implements
detection or policy logic::

    Repository --> Commit --> Analyzer(DetectionEngine + PolicyEvaluator)
               --> CommitReport --> ScanReport (text / JSON / future audit log)
"""
