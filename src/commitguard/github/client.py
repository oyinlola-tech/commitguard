"""GitHub API client (Phase 4, not implemented).

TODO(phase-4):
* explicit opt-in only; token read from the environment at call time, never
  logged, never written to configuration or audit records;
* least privilege (``checks: write``, ``contents: read``, ``pull-requests: read``);
* no repository content is uploaded - only check results and summaries;
* choose an HTTP client then (no dependency is added until it is needed).
"""
