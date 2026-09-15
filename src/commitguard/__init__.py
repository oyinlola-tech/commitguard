"""CommitGuard: Git commit provenance and contribution policy enforcement.

CommitGuard analyses Git commits, produces structured security *findings* with
independent *detectors*, and turns those findings into an ALLOW / WARN / BLOCK
*decision* using a separately configured *policy*.

Package layout (dependency direction flows downwards only)::

    cli          -> user interface (Typer); the only layer that prints
    core         -> detection engine, findings, decisions, scan context
    detectors    -> pure functions of a commit; never touch the repository
    policies     -> maps findings to actions
    provenance   -> identity / trailer / signature models and analysis
    git          -> safe Git CLI wrapper (isolated from detection)
    config       -> validated repository configuration
    github       -> GitHub Actions and GitHub App adapters (webhooks, Checks API)
    audit        -> audit events (recorded by the GitHub App service)
    observability-> structured logs, correlation IDs and metrics for services
    security     -> input validation, sanitisation, hashing
    exceptions   -> error hierarchy
    utils        -> subprocess / filesystem / platform helpers
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
