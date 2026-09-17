"""Isolated Git workspaces for benchmarks.

Benchmarks run real ``git`` processes in temporary directories with a private
``HOME`` and global configuration, so the developer's Git settings (hooks paths,
signing, aliases, credential helpers) can neither influence nor be modified by a
measurement.
"""

import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from commitguard.utils.subprocess import CommandResult, run_command

GIT_TIMEOUT = 600.0


@dataclass(frozen=True, slots=True)
class Workspace:
    root: Path
    env: Mapping[str, str]

    def git(
        self, cwd: Path, *args: str, input_bytes: bytes | None = None, check: bool = True
    ) -> CommandResult:
        result = run_command(
            ["git", *args],
            cwd=cwd,
            env_overrides=self.env,
            input_bytes=input_bytes,
            timeout=GIT_TIMEOUT,
        )
        if check and result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace")[-500:]
            raise RuntimeError(f"git {' '.join(args[:2])} failed ({result.returncode}): {detail}")
        return result

    def timed_git(
        self, cwd: Path, *args: str, input_bytes: bytes | None = None
    ) -> tuple[float, CommandResult]:
        started = time.perf_counter()
        result = self.git(cwd, *args, input_bytes=input_bytes, check=False)
        return time.perf_counter() - started, result

    def init(self, name: str, *, bare: bool = False) -> Path:
        path = self.root / name
        self.git(self.root, "init", "--quiet", *(["--bare"] if bare else []), str(path))
        return path


def _on_rm_error(function: Callable[[str], object], path: str, _error: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


@contextmanager
def workspace(prefix: str = "commitguard-bench-") -> Iterator[Workspace]:
    root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    home = root / "home"
    home.mkdir()
    config = home / ".gitconfig"
    config.write_text(
        "[user]\n\tname = Benchmark Developer\n\temail = dev@example.com\n"
        "[init]\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n"
        "[core]\n\tautocrlf = false\n",
        encoding="utf-8",
    )
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C.UTF-8",
    }
    try:
        yield Workspace(root=root, env=env)
    finally:
        shutil.rmtree(root, onexc=_on_rm_error)


def fast_import_stream(messages: Sequence[str], *, branch: str = "main") -> bytes:
    """A ``git fast-import`` stream creating one commit per message on ``branch``."""
    parts: list[bytes] = []
    timestamp = 1_700_000_000
    for index, message in enumerate(messages):
        data = message.encode("utf-8")
        content = f"change {index}\n".encode()
        parts.append(f"commit refs/heads/{branch}\n".encode())
        parts.append(f"mark :{index + 1}\n".encode())
        parts.append(f"author Ada Lovelace <ada@example.com> {timestamp + index} +0000\n".encode())
        parts.append(
            f"committer Ada Lovelace <ada@example.com> {timestamp + index} +0000\n".encode()
        )
        parts.append(f"data {len(data)}\n".encode() + data + b"\n")
        if index:
            parts.append(f"from :{index}\n".encode())
        parts.append(f"M 100644 inline file.txt\ndata {len(content)}\n".encode() + content)
        parts.append(b"\n")
    return b"".join(parts)
