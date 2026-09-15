"""Git hook installation, removal and integrity checking.

Installed hooks are tiny POSIX ``sh`` wrappers - they contain no detection or
policy logic. Each wrapper consists of a *managed block*::

    #!/bin/sh
    # BEGIN COMMITGUARD
    # commitguard-hook: pre-push
    # commitguard-format: 1
    # commitguard-checksum: sha256:<hex>
    ...runs `python -P -m commitguard hook pre-push "$@"`, then the chained hook...
    # END COMMITGUARD

Execution order inside a wrapper:

1. CommitGuard (``commitguard hook <name>``). A non-zero exit stops Git.
2. The *chained* hook ``<name>.pre-commitguard`` - the hook that existed
   before installation - with the same arguments and standard input. Its exit
   status is respected.

Safety properties:

* an existing, non-CommitGuard hook is never overwritten or deleted: it is
  atomically renamed to ``<name>.pre-commitguard`` and chained, and
  ``uninstall`` renames it back;
* ``uninstall`` removes only the managed block, never unrelated content;
* hooks are only installed inside the repository's own Git directory unless
  explicitly allowed, because a shared ``core.hooksPath`` affects other
  repositories;
* the wrapper runs Python with ``-P`` so a ``commitguard/`` directory in the
  repository being committed cannot shadow the real package;
* the block carries a checksum so ``commitguard doctor`` can detect edits;
* the Python interpreter that ran ``commitguard install`` is embedded (the
  only reliable way to find a virtualenv install when Git runs the hook from
  a different shell); if it disappears the wrapper falls back to
  ``commitguard`` on ``PATH`` and otherwise fails closed.
"""

import hashlib
import os
import sys
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from commitguard.config.loader import global_config_path
from commitguard.exceptions.git import GitCommandError, HookInstallError
from commitguard.git.commands import run_git
from commitguard.git.repository import Repository
from commitguard.utils.filesystem import atomic_write_text, read_text_limited
from commitguard.utils.platform import path_for_posix_shell, supports_executable_bit

BEGIN_MARKER = "# BEGIN COMMITGUARD"
END_MARKER = "# END COMMITGUARD"
FORMAT_VERSION = 1
CHAINED_SUFFIX = ".pre-commitguard"
SHEBANG = "#!/bin/sh"
MAX_HOOK_BYTES = 1024 * 1024
_CHECKSUM_PREFIX = "# commitguard-checksum: sha256:"
_PYTHON_PREFIX = "commitguard_python="


class HookType(StrEnum):
    """Git hooks CommitGuard manages."""

    PRE_COMMIT = "pre-commit"
    COMMIT_MSG = "commit-msg"
    PRE_PUSH = "pre-push"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def default_python() -> str:
    """The interpreter that should run CommitGuard from hooks (this one)."""
    return path_for_posix_shell(sys.executable)


def _body(hook: HookType, python: str) -> list[str]:
    if any(ch in python for ch in "\n\r\x00"):
        raise HookInstallError("interpreter path contains control characters")
    name = hook.value
    lines = [
        f"commitguard_hook={_sh_quote(name)}",
        f"{_PYTHON_PREFIX}{_sh_quote(python)}",
        f'commitguard_chained="$(dirname -- "$0")/{name}{CHAINED_SUFFIX}"',
        "",
        "commitguard_run() {",
        '    if [ -n "$commitguard_python" ] && [ -f "$commitguard_python" ]; then',
        '        "$commitguard_python" -P -m commitguard hook "$commitguard_hook" "$@"',
        "    elif command -v commitguard >/dev/null 2>&1; then",
        '        commitguard hook "$commitguard_hook" "$@"',
        "    else",
        '        echo "CommitGuard is not available." >&2',
        '        echo "The repository\'s $commitguard_hook security hook could not execute." >&2',
        '        echo "Operation blocked because the security check could not be completed." >&2',
        '        echo "Run: commitguard doctor   (reinstall hooks with: commitguard install)" >&2',
        "        return 2",
        "    fi",
        "}",
        "",
    ]
    if hook is HookType.PRE_PUSH:
        # stdin (the ref updates) must reach both CommitGuard and the chained hook.
        lines += [
            "commitguard_stdin=$(cat; echo .)",
            "commitguard_stdin=${commitguard_stdin%.}",
            'printf \'%s\' "$commitguard_stdin" | commitguard_run "$@" || exit $?',
            'if [ -x "$commitguard_chained" ]; then',
            '    printf \'%s\' "$commitguard_stdin" | "$commitguard_chained" "$@" || exit $?',
            "fi",
        ]
    else:
        lines += [
            'commitguard_run "$@" || exit $?',
            'if [ -x "$commitguard_chained" ]; then',
            '    "$commitguard_chained" "$@" || exit $?',
            "fi",
        ]
    return lines


def _checksum(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def render_block(hook: HookType, python: str) -> str:
    """The managed block (markers included) for ``hook``."""
    header = [
        "# This block is managed by CommitGuard. Do not edit it: `commitguard doctor`",
        "# detects changes and `commitguard install` restores it. `commitguard uninstall`",
        "# removes it and restores any hook preserved during installation.",
        f"# commitguard-hook: {hook.value}",
        f"# commitguard-format: {FORMAT_VERSION}",
    ]
    body = _body(hook, python)
    checksum_line = f"{_CHECKSUM_PREFIX}{_checksum(header + body)}"
    return "\n".join([BEGIN_MARKER, *header, checksum_line, *body, END_MARKER]) + "\n"


def render_hook_file(hook: HookType, python: str) -> str:
    return f"{SHEBANG}\n{render_block(hook, python)}"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
class ManagedBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int  # line index of BEGIN marker
    end: int  # line index of END marker
    hook: str | None
    python: str | None
    intact: bool  # checksum matches


def _unquote_sh(value: str) -> str | None:
    if len(value) < 2 or not (value.startswith("'") and value.endswith("'")):
        return None
    return value[1:-1].replace("'\\''", "'")


def parse_managed_block(text: str) -> ManagedBlock | None:
    """Locate CommitGuard's block. Raises :class:`HookInstallError` if markers are corrupt."""
    lines = text.split("\n")
    begins = [i for i, line in enumerate(lines) if line.rstrip("\r") == BEGIN_MARKER]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r") == END_MARKER]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise HookInstallError("CommitGuard hook markers are corrupt")
    start, end = begins[0], ends[0]
    inner = [line.rstrip("\r") for line in lines[start + 1 : end]]
    checksum_lines = [line for line in inner if line.startswith(_CHECKSUM_PREFIX)]
    content = [line for line in inner if not line.startswith(_CHECKSUM_PREFIX)]
    intact = (
        len(checksum_lines) == 1 and checksum_lines[0] == f"{_CHECKSUM_PREFIX}{_checksum(content)}"
    )
    hook = next(
        (line.split(":", 1)[1].strip() for line in inner if line.startswith("# commitguard-hook:")),
        None,
    )
    python = next(
        (
            _unquote_sh(line[len(_PYTHON_PREFIX) :])
            for line in inner
            if line.startswith(_PYTHON_PREFIX)
        ),
        None,
    )
    return ManagedBlock(start=start, end=end, hook=hook, python=python, intact=intact)


def _remove_block(text: str, block: ManagedBlock) -> str:
    lines = text.split("\n")
    return "\n".join(lines[: block.start] + lines[block.end + 1 :])


def _only_shebang(text: str) -> bool:
    meaningful = [line for line in text.split("\n") if line.strip()]
    return not meaningful or (len(meaningful) == 1 and meaningful[0].startswith("#!"))


def _read(path: Path) -> str:
    try:
        return read_text_limited(path, max_bytes=MAX_HOOK_BYTES)
    except (OSError, ValueError) as exc:
        raise HookInstallError(f"cannot read hook {path}: {exc}") from exc


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _write_hook(path: Path, content: str, *, overwrite: bool) -> None:
    try:
        atomic_write_text(path, content, overwrite=overwrite, mode=0o755)
    except FileExistsError as exc:
        raise HookInstallError(f"{path} appeared during installation; not overwriting") from exc
    except OSError as exc:
        raise HookInstallError(f"cannot write hook {path}: {exc.strerror}") from exc


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
class HookState(StrEnum):
    MISSING = "missing"
    INSTALLED = "installed"
    OUTDATED = "outdated"  # managed and intact, but generated for another interpreter/format
    MODIFIED = "modified"  # managed block edited (checksum mismatch)
    CORRUPT = "corrupt"  # markers damaged
    FOREIGN = "foreign"  # a hook exists that CommitGuard does not manage
    NOT_EXECUTABLE = "not_executable"  # Git will silently skip it


class HookStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hook: HookType
    path: Path
    state: HookState
    python: str | None = None
    python_available: bool | None = None
    chained: Path | None = None


def hook_status(
    hooks_dir: Path, hook: HookType, *, expected_python: str | None = None
) -> HookStatus:
    path = hooks_dir / hook.value
    chained_path = hooks_dir / f"{hook.value}{CHAINED_SUFFIX}"
    chained = chained_path if _exists(chained_path) else None
    if not _exists(path):
        return HookStatus(hook=hook, path=path, state=HookState.MISSING, chained=chained)
    try:
        block = parse_managed_block(_read(path))
    except HookInstallError:
        return HookStatus(hook=hook, path=path, state=HookState.CORRUPT, chained=chained)
    if block is None:
        return HookStatus(hook=hook, path=path, state=HookState.FOREIGN, chained=chained)

    python_available = bool(block.python) and Path(block.python or "").is_file()
    if not block.intact or block.hook != hook.value:
        state = HookState.MODIFIED
    elif supports_executable_bit() and not os.access(path, os.X_OK):
        state = HookState.NOT_EXECUTABLE
    elif expected_python is not None and _read(path).find(render_block(hook, expected_python)) < 0:
        state = HookState.OUTDATED
    else:
        state = HookState.INSTALLED
    return HookStatus(
        hook=hook,
        path=path,
        state=state,
        python=block.python,
        python_available=python_available,
        chained=chained,
    )


# --------------------------------------------------------------------------- #
# Install / uninstall
# --------------------------------------------------------------------------- #
class InstallAction(StrEnum):
    INSTALLED = "installed"
    CHAINED = "installed_and_chained"  # existing hook preserved and chained
    UPDATED = "updated"
    UNCHANGED = "unchanged"


class InstallResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hook: HookType
    path: Path
    action: InstallAction
    chained: Path | None = None


class UninstallAction(StrEnum):
    REMOVED = "removed"
    RESTORED = "removed_and_restored"  # previously existing hook put back
    BLOCK_REMOVED = "block_removed"  # other content in the file was kept
    NOT_INSTALLED = "not_installed"
    FOREIGN = "foreign_untouched"


class UninstallResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hook: HookType
    path: Path
    action: UninstallAction
    note: str = ""


def install_hook(hooks_dir: Path, hook: HookType, python: str) -> InstallResult:
    """Install or repair one hook in ``hooks_dir`` without destroying existing hooks."""
    path = hooks_dir / hook.value
    chained = hooks_dir / f"{hook.value}{CHAINED_SUFFIX}"
    desired_block = render_block(hook, python)

    if path.is_dir():
        raise HookInstallError(f"{path} is a directory")

    if not _exists(path):
        _write_hook(path, f"{SHEBANG}\n{desired_block}", overwrite=False)
        return InstallResult(
            hook=hook,
            path=path,
            action=InstallAction.INSTALLED,
            chained=chained if _exists(chained) else None,
        )

    text = _read(path)
    block = parse_managed_block(text)
    if block is not None:
        lines = text.split("\n")
        current = "\n".join(lines[block.start : block.end + 1]) + "\n"
        if current == desired_block and (not supports_executable_bit() or os.access(path, os.X_OK)):
            return InstallResult(
                hook=hook,
                path=path,
                action=InstallAction.UNCHANGED,
                chained=chained if _exists(chained) else None,
            )
        new_text = "\n".join(lines[: block.start]) + ("\n" if block.start else "")
        new_text += desired_block + "\n".join(lines[block.end + 1 :])
        _write_hook(path, new_text, overwrite=True)
        return InstallResult(
            hook=hook,
            path=path,
            action=InstallAction.UPDATED,
            chained=chained if _exists(chained) else None,
        )

    # A foreign hook: preserve it by renaming, then chain it.
    if _exists(chained):
        raise HookInstallError(
            f"{path} is not managed by CommitGuard and {chained.name} already exists; "
            "refusing to overwrite either. Merge them manually, then rerun install."
        )
    os.replace(path, chained)
    try:
        _write_hook(path, f"{SHEBANG}\n{desired_block}", overwrite=False)
    except BaseException:
        os.replace(chained, path)  # roll back: the original hook is back in place
        raise
    return InstallResult(hook=hook, path=path, action=InstallAction.CHAINED, chained=chained)


def uninstall_hook(hooks_dir: Path, hook: HookType) -> UninstallResult:
    """Remove only CommitGuard's block; restore a preserved hook where possible."""
    path = hooks_dir / hook.value
    chained = hooks_dir / f"{hook.value}{CHAINED_SUFFIX}"
    if not _exists(path):
        return UninstallResult(hook=hook, path=path, action=UninstallAction.NOT_INSTALLED)
    text = _read(path)
    block = parse_managed_block(text)
    if block is None:
        return UninstallResult(hook=hook, path=path, action=UninstallAction.FOREIGN)

    remaining = _remove_block(text, block)
    if not _only_shebang(remaining):
        _write_hook(path, remaining, overwrite=True)
        note = (
            f"{chained.name} was left in place because {path.name} still has other content"
            if _exists(chained)
            else ""
        )
        return UninstallResult(
            hook=hook, path=path, action=UninstallAction.BLOCK_REMOVED, note=note
        )

    if _exists(chained):
        os.replace(chained, path)  # atomic: the preserved hook replaces the wrapper
        return UninstallResult(hook=hook, path=path, action=UninstallAction.RESTORED)
    path.unlink()
    return UninstallResult(hook=hook, path=path, action=UninstallAction.REMOVED)


def repository_hooks_dir(repository: Repository, *, allow_shared: bool = False) -> Path:
    """The hooks directory to manage, refusing shared locations unless allowed."""
    hooks_dir = repository.hooks_dir()
    if not allow_shared:
        try:
            hooks_dir.resolve().relative_to(repository.common_dir.resolve())
        except ValueError:
            raise HookInstallError(
                f"core.hooksPath points outside this repository's Git directory ({hooks_dir}). "
                "Installing there would affect other repositories or tracked files. "
                "Rerun with --allow-shared-hooks-path if that is intended."
            ) from None
    return hooks_dir


def _selected(hooks: tuple[HookType, ...] | None) -> tuple[HookType, ...]:
    return hooks if hooks else tuple(HookType)


def install_hooks(
    repository: Repository,
    hooks: tuple[HookType, ...] | None = None,
    *,
    python: str | None = None,
    allow_shared_hooks_path: bool = False,
) -> list[InstallResult]:
    hooks_dir = repository_hooks_dir(repository, allow_shared=allow_shared_hooks_path)
    hooks_dir.mkdir(parents=True, exist_ok=True)
    interpreter = python if python is not None else default_python()
    return [install_hook(hooks_dir, hook, interpreter) for hook in _selected(hooks)]


def uninstall_hooks(
    repository: Repository,
    hooks: tuple[HookType, ...] | None = None,
    *,
    allow_shared_hooks_path: bool = False,
) -> list[UninstallResult]:
    hooks_dir = repository_hooks_dir(repository, allow_shared=allow_shared_hooks_path)
    return [uninstall_hook(hooks_dir, hook) for hook in _selected(hooks)]


# --------------------------------------------------------------------------- #
# Global installation (Git template directory)
# --------------------------------------------------------------------------- #
# ``core.hooksPath`` is deliberately NOT used: setting it globally would stop
# every repository's own .git/hooks from running. Instead, hooks are placed in
# a Git template directory, which ``git init`` and ``git clone`` copy into
# *new* repositories only. Existing repositories need ``commitguard install``.


def global_template_dir() -> Path:
    return global_config_path().parent / "git-template"


def _global_template_setting() -> str | None:
    result = run_git(["config", "--global", "--get", "init.templateDir"], check=False)
    if result.returncode == 1:
        return None
    if not result.ok:
        raise GitCommandError(result.args, result.returncode, "git config failed")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _same_path(configured: str, path: Path) -> bool:
    try:
        return Path(configured).expanduser().resolve() == path.resolve()
    except OSError:
        return False


def install_global(
    hooks: tuple[HookType, ...] | None = None, *, python: str | None = None
) -> tuple[Path, list[InstallResult], bool]:
    """Install hooks into the CommitGuard Git template directory.

    Returns ``(template_dir, results, configured)`` where ``configured`` is True
    if ``init.templateDir`` was set by this call. Refuses to replace a
    different, user-configured template directory.
    """
    template = global_template_dir()
    current = _global_template_setting()
    if current is not None and not _same_path(current, template):
        raise HookInstallError(
            f"global init.templateDir is already set to {current!r}; not changing it. "
            "Add CommitGuard hooks to that template manually or use per-repository install."
        )
    hooks_dir = template / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    interpreter = python if python is not None else default_python()
    results = []
    for hook in _selected(hooks):
        path = hooks_dir / hook.value
        if _exists(path) and parse_managed_block(_read(path)) is None:
            raise HookInstallError(f"{path} exists and is not managed by CommitGuard")
        results.append(install_hook(hooks_dir, hook, interpreter))
    configured = False
    if current is None:
        run_git(["config", "--global", "init.templateDir", path_for_posix_shell(str(template))])
        configured = True
    return template, results, configured


def uninstall_global(
    hooks: tuple[HookType, ...] | None = None,
) -> tuple[Path, list[UninstallResult], bool]:
    """Remove CommitGuard template hooks; unset init.templateDir only if it is ours."""
    template = global_template_dir()
    hooks_dir = template / "hooks"
    results = (
        [uninstall_hook(hooks_dir, hook) for hook in _selected(hooks)] if hooks_dir.is_dir() else []
    )
    unset = False
    current = _global_template_setting()
    remaining = hooks_dir.is_dir() and any(hooks_dir.iterdir())
    if current is not None and _same_path(current, template) and not remaining:
        run_git(["config", "--global", "--unset", "init.templateDir"])
        unset = True
    return template, results, unset
