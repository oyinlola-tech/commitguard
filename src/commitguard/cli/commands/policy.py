"""``commitguard policy``: inspect policies."""

import typer

from commitguard.cli.output import handled_errors, info, table
from commitguard.config.loader import load_repository_config
from commitguard.config.schema import CommitGuardConfig
from commitguard.exceptions.git import NotAGitRepositoryError
from commitguard.git.repository import Repository
from commitguard.policies.loader import build_policy_set

policy_app = typer.Typer(help="Inspect policies.", no_args_is_help=True)


@policy_app.command("list")
def list_command() -> None:
    """Show the effective policies for the current repository."""
    with handled_errors():
        try:
            repository = Repository.discover()
        except NotAGitRepositoryError:
            config, source = CommitGuardConfig(version=1), None
            origin = "built-in defaults (not inside a Git repository)"
        else:
            config, source = load_repository_config(repository.root)
            origin = str(source) if source else "built-in defaults (no .commitguard.yaml found)"

        policies = build_policy_set(config)

    info(f"Configuration: {origin}")
    info("")
    rows = [
        [p.id, "yes" if p.enabled else "no", p.action.value, p.description]
        for p in policies.values()
    ]
    info(table(["policy", "enabled", "action", "description"], rows))
