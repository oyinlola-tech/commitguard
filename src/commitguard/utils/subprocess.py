"""Safe subprocess execution.

Rules enforced here, for every external command CommitGuard runs:

* commands are argument sequences, never shell strings (``shell=False``);
* arguments must be ``str`` and must not contain NUL bytes;
* stdin is closed unless input is explicitly supplied;
* every call has a timeout;
* the environment is inherited but can only be *extended* with explicit keys,
  and is never logged.
"""

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from commitguard.exceptions.base import UnsafeInputError

DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Raw result of an external command. Output is kept as bytes."""

    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env_overrides: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run an external command without a shell and capture its output.

    Raises :class:`UnsafeInputError` for malformed argument vectors and lets
    :class:`FileNotFoundError` / :class:`subprocess.TimeoutExpired` propagate so
    callers can map them to domain errors.
    """
    if isinstance(args, str | bytes):
        raise UnsafeInputError("command must be an argument sequence, not a string")
    argv = tuple(args)
    if not argv:
        raise UnsafeInputError("command must not be empty")
    for arg in argv:
        if not isinstance(arg, str):
            raise UnsafeInputError("command arguments must be strings")
        if "\x00" in arg:
            raise UnsafeInputError("command arguments must not contain NUL bytes")

    env = dict(os.environ)
    if env_overrides:
        env.update(env_overrides)

    # TODO(phase-2): cap captured output size for very large repositories.
    completed = subprocess.run(  # noqa: S603 - argv is validated and shell=False
        argv,
        cwd=cwd,
        env=env,
        input=input_bytes,
        stdin=None if input_bytes is not None else subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    return CommandResult(
        args=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
