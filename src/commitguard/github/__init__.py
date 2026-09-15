"""GitHub integration: GitHub Actions (Phase 4) and the CommitGuard GitHub App (Phase 5).

Both are adapters around the same core: they turn GitHub events into a
:class:`~commitguard.ci.context.CIContext`, run
:class:`~commitguard.services.scan.ScanService` (the detection engine and policy
evaluator shared with the CLI and Git hooks) and translate the result into
GitHub output. Nothing in this package detects anything or decides policy.

GitHub Actions (no network, no token):

* :mod:`~commitguard.github.events`    - event payloads -> ``CIContext``;
* :mod:`~commitguard.github.actions`   - workflow commands, job summary, outputs;
* :mod:`~commitguard.github.checks`    - check output model and conclusions;
* :mod:`~commitguard.github.workflow`  - workflow template and static inspection.

GitHub App (webhooks, Checks API; needs the optional ``app`` extra):

* :mod:`~commitguard.github.settings`      - environment configuration;
* :mod:`~commitguard.github.auth`          - App JWT, down-scoped installation tokens;
* :mod:`~commitguard.github.client`        - small REST client (retries, rate limits);
* :mod:`~commitguard.github.webhooks`      - signature, header, size and JSON validation;
* :mod:`~commitguard.github.events`        - webhook normalisation (typed events);
* :mod:`~commitguard.github.installations` - installation lifecycle and authorization;
* :mod:`~commitguard.github.repositories`  - metadata-only Git mirrors (no checkout);
* :mod:`~commitguard.github.storage`       - deliveries, installations, jobs, audit (SQLite);
* :mod:`~commitguard.github.queue`         - event queue abstraction;
* :mod:`~commitguard.github.worker`        - scan worker and Check Run lifecycle;
* :mod:`~commitguard.github.check_runs`    - Check Run content;
* :mod:`~commitguard.github.app`           - service wiring and WSGI endpoint;
* :mod:`~commitguard.github.server`        - development HTTP server.

A failing check blocks merges only when branch protection or a ruleset
requires it; CommitGuard does not configure or verify those settings.
"""
