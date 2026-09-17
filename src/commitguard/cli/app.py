"""Typer application and console entry point."""

from typing import Annotated

import typer

from commitguard import __version__
from commitguard.cli.commands import (
    benchmark,
    check,
    ci,
    dashboard,
    doctor,
    github,
    hook,
    init,
    install,
    policy,
    report,
    reproduce,
    scan,
)

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
app.add_typer(hook.hook_app, name="hook")
app.add_typer(ci.ci_app, name="ci")
app.add_typer(github.github_app, name="github")
app.add_typer(dashboard.dashboard_app, name="dashboard")
app.add_typer(benchmark.benchmark_app, name="benchmark")
app.add_typer(reproduce.reproduce_app, name="reproduce")
app.add_typer(report.report_app, name="report")


def main() -> None:
    """Console script entry point (``commitguard``)."""
    app()
