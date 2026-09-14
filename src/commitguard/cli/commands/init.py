"""``commitguard init``: create a repository configuration file."""

from commitguard.cli.output import fail, handled_errors, info
from commitguard.config.defaults import DEFAULT_CONFIG_FILENAME, DEFAULT_CONFIG_TEMPLATE
from commitguard.config.loader import find_config
from commitguard.git.repository import Repository
from commitguard.utils.filesystem import atomic_write_text


def init_command() -> None:
    """Create .commitguard.yaml with secure defaults in the current repository.

    An existing configuration file is never overwritten.
    """
    with handled_errors():
        repository = Repository.discover()
        existing = find_config(repository.root)
        if existing is not None:
            fail(f"configuration already exists: {existing}")

        target = repository.root / DEFAULT_CONFIG_FILENAME
        try:
            atomic_write_text(target, DEFAULT_CONFIG_TEMPLATE)
        except FileExistsError:
            fail(f"configuration already exists: {target}")
        except OSError as exc:
            fail(f"could not write {target}: {exc.strerror}")

    info(f"Created {target}")
    info("Next: review the policies, then commit the file.")
    info("Then run `commitguard scan` to analyse the current commit.")
    info("Note: `commitguard install` (Git hooks) is not implemented yet (Phase 3).")
