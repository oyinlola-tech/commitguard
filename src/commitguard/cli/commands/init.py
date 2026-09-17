"""``commitguard init``: set a repository up for enforcement.

Writes ``.commitguard.yaml``, and can install the Git hooks and write a GitHub
workflow. Existing files are never overwritten.

Interactive by default when the terminal can answer (a TTY), and fully
non-interactive otherwise, so scripts and CI behave the same as ``--non-interactive``:
nothing is prompted, and only what the options ask for is done.
"""

import sys
from pathlib import Path
from typing import Annotated

import typer

from commitguard.cli.output import fail, handled_errors, info
from commitguard.config.defaults import DEFAULT_CONFIG_FILENAME, DEFAULT_CONFIG_TEMPLATE
from commitguard.config.loader import find_config
from commitguard.git.repository import Repository
from commitguard.github.workflow import CHECK_NAME, WORKFLOW_FILE, render_workflow
from commitguard.utils.filesystem import atomic_write_text


def _write_new(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
    except FileExistsError:
        fail(f"{path} already exists; not overwriting it")
    except OSError as exc:
        fail(f"could not write {path}: {exc.strerror}")


def _ask(question: str, *, default: bool) -> bool:
    return bool(typer.confirm(question, default=default))


def init_command(
    github: Annotated[
        bool,
        typer.Option("--github", help=f"Also create {WORKFLOW_FILE.as_posix()}."),
    ] = False,
    install: Annotated[
        bool,
        typer.Option(
            "--install-hooks", help="Also install the Git hooks (as `commitguard install`)."
        ),
    ] = False,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="Never prompt; do exactly what the options say (the default when not a terminal).",
        ),
    ] = False,
    action_repository: Annotated[
        str | None,
        typer.Option(
            "--action-repository",
            help="Repository hosting the CommitGuard Action (OWNER/REPO), used with --github.",
        ),
    ] = None,
    action_ref: Annotated[
        str | None,
        typer.Option(
            "--action-ref",
            help="Full commit SHA of the CommitGuard Action to pin, used with --github.",
        ),
    ] = None,
) -> None:
    """Create .commitguard.yaml, and optionally install hooks and a GitHub workflow."""
    interactive = not non_interactive and sys.stdin.isatty() and sys.stdout.isatty()
    with handled_errors():
        repository = Repository.discover()
        if interactive:
            info(f"Setting up CommitGuard in {repository.root}")
            if not github and not (action_repository or action_ref):
                github = _ask(
                    "Add a GitHub Actions check (recommended: it cannot be bypassed)?",
                    default=False,
                )
                if github:
                    action_repository = action_repository or typer.prompt(
                        "Repository hosting the CommitGuard Action (OWNER/REPO)"
                    )
                    action_ref = action_ref or typer.prompt(
                        "Commit SHA of the Action to pin (40 characters)"
                    )
            if not install:
                install = _ask("Install the Git hooks in this repository now?", default=True)
        workflow_path = repository.root / WORKFLOW_FILE
        workflow_text = None
        if github:
            if not action_repository or not action_ref:
                fail(
                    "--github requires --action-repository OWNER/REPO and --action-ref "
                    "<40-character commit SHA> (the Action is pinned to an exact commit)"
                )
            if workflow_path.exists() or workflow_path.is_symlink():
                fail(f"{workflow_path} already exists; not overwriting it")
            workflow_text = render_workflow(action_repository, action_ref)
        elif action_repository or action_ref:
            fail("--action-repository and --action-ref are only used with --github")

        existing = find_config(repository.root)
        if existing is not None and not github:
            fail(f"configuration already exists: {existing}")

        created = []
        if existing is None:
            target = repository.root / DEFAULT_CONFIG_FILENAME
            _write_new(target, DEFAULT_CONFIG_TEMPLATE)
            created.append(target)
        if workflow_text is not None:
            _write_new(workflow_path, workflow_text)
            created.append(workflow_path)

    for path in created:
        info(f"Created {path}")
    if install:
        from commitguard.cli.commands.install import install_command

        info("")
        install_command()
        info("")
    if existing is not None:
        info(f"Kept existing configuration: {existing}")
    info("Next: review and commit the files.")
    if github:
        info(
            f'Then require the "{CHECK_NAME}" status check on protected branches '
            "(see `commitguard github setup`); the workflow alone does not block merges."
        )
    else:
        if not install:
            info("Run `commitguard install` to enforce policies in local Git hooks.")
