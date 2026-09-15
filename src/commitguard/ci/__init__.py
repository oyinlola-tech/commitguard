"""Provider-neutral CI enforcement.

CI providers (GitHub Actions today; GitLab, Bitbucket, Azure DevOps later)
translate their event data into a :class:`~commitguard.ci.context.CIContext`.
Everything after that - commit range, trusted policy source, detection, policy
evaluation - is shared and lives in :mod:`commitguard.services.ci`.
"""
