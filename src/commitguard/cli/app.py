"""Typer application and console entry point."""

from typing import Annotated

import typer

from commitguard import __version__
from commitguard.cli.commands import check, doctor, init, install, policy, scan

app = typer.Typer(
    name="commitguard",
    help="Git commit provenance and contribution policy enforcement.",
    no_args_is_help=True,
    add_completion=False,
    # Never render local variables in tracebacks: they may hold secrets.
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"commitguard {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """CommitGuard analyses commits and enforces repository contribution policies."""


app.command("init")(init.init_command)
app.command("install")(install.install_command)
app.command("uninstall")(install.uninstall_command)
app.command("scan")(scan.scan_command)
app.command("check")(check.check_command)
app.command("doctor")(doctor.doctor_command)
app.add_typer(policy.policy_app, name="policy")


def main() -> None:
    """Console script entry point (``commitguard``)."""
    app()
