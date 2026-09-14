"""Platform and runtime introspection."""

import platform
import sys

MINIMUM_PYTHON = (3, 12)


def python_version() -> str:
    """Return the running interpreter version as ``X.Y.Z``."""
    return platform.python_version()


def python_version_supported() -> bool:
    """Return True if the running interpreter meets :data:`MINIMUM_PYTHON`."""
    return sys.version_info[:2] >= MINIMUM_PYTHON


def is_windows() -> bool:
    """Return True on Windows (affects hook scripts and executable bits)."""
    return sys.platform.startswith("win")


def path_for_posix_shell(path: str) -> str:
    """Render a filesystem path for use inside a POSIX ``sh`` script.

    Git for Windows runs hooks with its bundled ``sh``, which accepts
    ``C:/Users/...`` style paths; backslashes would be escape characters.
    """
    return path.replace("\\", "/") if is_windows() else path


def supports_executable_bit() -> bool:
    """False on Windows, where Git decides executability from the ``#!`` line."""
    return not is_windows()
