"""The CommitGuard control plane: what the dashboard API exposes.

::

    CommitGuard core (detection, policy)  ->  ScanService -> ScanResult
            |                                                   |
            |                        ScanResultRecorder (findings, violation lifecycle)
            v                                                   v
    OrganizationPolicyService  ---- mandatory floor ---->  state database
            ^                                                   ^
            |                                                   |
    dashboard API routes -> authentication -> authorization -> query/command services

Nothing here detects attribution or evaluates policy. Scans are produced by
the same :class:`~commitguard.services.scan.ScanService` the GitHub Action and
the GitHub App use; this package stores their results, tracks whether each
violation is still present, versions organisation policy, and answers
questions about that data for one authorised principal at a time.

Tenant isolation is structural: every read takes an :class:`~commitguard.controlplane.access.AccessScope`
built from the signed-in session, and every query filters by the installations
and repositories in that scope.
"""
