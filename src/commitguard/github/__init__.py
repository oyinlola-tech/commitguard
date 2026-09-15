"""GitHub enforcement layer (GitHub Actions).

Local hooks can be bypassed (``--no-verify``, deleting hooks, pushing from
another clone). This package adapts GitHub Actions to the shared CI service so
the same detection and policy engine runs on GitHub:

* :mod:`~commitguard.github.events`   - event payloads -> ``CIContext``;
* :mod:`~commitguard.github.actions`  - workflow commands, job summary, outputs;
* :mod:`~commitguard.github.checks`   - check-run shaped output (future Checks API / App);
* :mod:`~commitguard.github.workflow` - workflow template and static inspection;
* :mod:`~commitguard.github.client`   - placeholder for a future API client.

Nothing here performs network access or needs a token. The check becomes a
merge gate only when branch protection or a ruleset requires it.
"""
