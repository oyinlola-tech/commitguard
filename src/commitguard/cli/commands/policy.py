"""``commitguard policy``: inspect policies."""

import typer

from commitguard.cli.common import ConfigOption
from commitguard.cli.output import handled_errors, info, table
from commitguard.config.loader import load_effective_config
from commitguard.exceptions.git import NotAGitRepositoryError
from commitguard.git.repository import Repository
from commitguard.policies.loader import build_policy_set

policy_app = typer.Typer(help="Inspect policies.", no_args_is_help=True)


@policy_app.command("list")
def list_command(config: ConfigOption = None) -> None:
    """Show the effective policies and the configuration layers they came from."""
    with handled_errors():
        try:
            root = Repository.discover().root
        except NotAGitRepositoryError:
            root = None
        loaded = load_effective_config(root, explicit_path=config)
        policies = build_policy_set(*loaded.configs)

    info("Configuration layers (lowest precedence first):")
    for source in loaded.sources:
        info(f"  - {source}")
    if root is None:
        info("  (not inside a Git repository: no repository layer)")
    info("")
    rows = [
        [p.id, "yes" if p.enabled else "no", p.action.value, p.description]
        for p in policies.values()
    ]
    info(table(["policy", "enabled", "action", "description"], rows))
