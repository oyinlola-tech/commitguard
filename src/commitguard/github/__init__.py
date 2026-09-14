"""GitHub enforcement layer (Phase 4, not implemented).

Local hooks can be bypassed (``--no-verify``, deleting the hook, committing
from another machine). Repository-level enforcement runs CommitGuard where the
developer cannot skip it: a required GitHub Actions check on pull requests
combined with branch protection.

Planned scope: GitHub Actions integration, Checks API reporting, pull request
validation, repository policy validation and branch protection guidance.

The local tool never requires GitHub authentication, and nothing in this
package performs network access today.
"""
