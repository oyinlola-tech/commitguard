"""The single choke point through which CommitGuard executes Git.

Hardening applied to every invocation:

* ``git`` is resolved once via ``PATH`` and invoked with an argument vector;
* ``--no-pager`` and ``GIT_TERMINAL_PROMPT=0``: never block on a pager/prompt;
* ``GIT_NO_REPLACE_OBJECTS=1``: ``git replace`` refs cannot substitute the
  commit we inspect with a different, innocent-looking one;
* ``GIT_OPTIONAL_LOCKS=0``: read-only queries do not refresh the index;
* ``LC_ALL=C``: stable, parseable error messages.

Callers that pass revisions must place them after ``--end-of-options``.
"""

import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

from commitguard.exceptions.git import GitCommandError, GitError, GitNotFoundError
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.utils.subprocess import DEFAULT_TIMEOUT_SECONDS, CommandResult, run_command

# ``rev-parse --end-of-options`` and ``--path-format=absolute`` need Git 2.31.
MINIMUM_GIT_VERSION = (2, 31)

GIT_ENV_OVERRIDES: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}

_VERSION_RE = re.compile(rb"git version (\d+)\.(\d+)(?:\.(\d+))?")


@cache
def git_executable() -> str:
    """Return the absolute path of the ``git`` executable."""
    path = shutil.which("git")
    if path is None:
        raise GitNotFoundError("git executable not found on PATH")
    return path


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    input_bytes: bytes | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run ``git <args>`` with CommitGuard's hardening applied.

    With ``check=True`` a non-zero exit raises :class:`GitCommandError` whose
    message contains sanitised stderr only. ``extra_env`` may add variables but
    never replace the hardening in :data:`GIT_ENV_OVERRIDES`.
    """
    argv = [git_executable(), "--no-pager", *args]
    env = dict(GIT_ENV_OVERRIDES)
    if extra_env:
        clash = sorted(set(extra_env) & set(GIT_ENV_OVERRIDES))
        if clash:
            raise ValueError(f"extra_env must not override git hardening: {', '.join(clash)}")
        env.update(extra_env)
    try:
        result = run_command(
            argv,
            cwd=cwd,
            env_overrides=env,
            timeout=timeout,
            input_bytes=input_bytes,
        )
    except FileNotFoundError as exc:
        raise GitNotFoundError("git executable could not be started") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git command timed out after {timeout:g}s") from exc

    if check and not result.ok:
        stderr = sanitize_for_terminal(result.stderr.decode("utf-8", errors="replace").strip())
        raise GitCommandError(argv, result.returncode, stderr)
    return result


def git_version() -> tuple[int, int, int]:
    """Return the installed Git version as ``(major, minor, patch)``."""
    result = run_git(["--version"])
    match = _VERSION_RE.search(result.stdout)
    if match is None:
        raise GitError("could not determine git version")
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def git_version_supported() -> bool:
    """Return True if the installed Git meets :data:`MINIMUM_GIT_VERSION`."""
    return git_version()[:2] >= MINIMUM_GIT_VERSION
