"""GitHub API client (not implemented).

Phase 4 enforcement deliberately works without the API: GitHub Actions
provides the event, the checkout and the check result. A future GitHub App or
Checks API integration (PR comments, organisation policies, verifying branch
protection) would live here, with these constraints:

* explicit opt-in only; token read from the environment at call time, never
  logged, never written to configuration or audit records;
* least privilege per feature (e.g. ``checks: write`` only for Checks API output);
* no repository content is uploaded - only check results and summaries.
"""
