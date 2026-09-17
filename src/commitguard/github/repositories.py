"""Metadata-only repository mirrors for server-side scanning.

The GitHub App needs the same commit objects the GitHub Action and the Git
hooks analyse, but it must never check out, build or run repository code. Each
repository gets a *bare, partial* mirror under the App's data directory::

    <data_dir>/mirrors/<installation_id>/<repository_id>.git

* paths use numeric IDs only (no repository names: no traversal, no renames);
* ``git init --bare --template=``: no hooks, no work tree, nothing checked out;
* ``--filter=blob:none``: commits and trees are fetched, **file contents are
  not** - only the CommitGuard configuration blobs at the commits being
  evaluated are fetched explicitly by object ID;
* the exact SHAs from the (verified) event are fetched, never branch names
  chosen by a payload; the default branch comes from the GitHub API;
* hardening per fetch: ``protocol.allow=never`` except the allowed protocol,
  no redirects, no credential helpers, no submodules, no tags, hooks path
  pointing at the null device, automatic GC and maintenance off;
* the installation token is passed as an HTTP header through ``GIT_CONFIG_*``
  environment variables (not in the URL, the command line or the mirror's
  config) and is registered for redaction;
* every later Git read uses ``GIT_NO_LAZY_FETCH=1``, so analysis never touches
  the network.
"""

import base64
import os
import shutil
import stat
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

from commitguard.config.defaults import CONFIG_FILENAMES
from commitguard.exceptions.git import GitCommandError, GitError
from commitguard.exceptions.service import InfrastructureError, ScanError
from commitguard.git.commands import git_version, run_git
from commitguard.git.repository import Repository
from commitguard.github.errors import safe_text
from commitguard.github.identifiers import RepositoryRef
from commitguard.security.secrets import Secret, register_secret
from commitguard.security.validation import is_git_sha, validate_repository_path

MIRROR_GIT_ENV = {"GIT_NO_LAZY_FETCH": "1"}
# GIT_NO_LAZY_FETCH (Git 2.45) is what keeps analysis of a partial mirror offline;
# older Git silently ignores it, so the App refuses to run with it.
MINIMUM_MIRROR_GIT_VERSION = (2, 45)
DEFAULT_FETCH_TIMEOUT_SECONDS = 600.0
LAST_USED_MARKER = "commitguard-last-used"
_REF_NAME_MAX = 255


def _clear_read_only_and_retry(
    function: Callable[[str], object], path: str, _error: BaseException
) -> None:
    """Git writes pack files read-only; on Windows they cannot be deleted until writable."""
    os.chmod(path, stat.S_IWRITE)
    function(path)


class FetchTimeoutError(InfrastructureError):
    """Fetching commits from GitHub took longer than the configured limit."""


class RemoteLocator(Protocol):
    def url_for(self, repository: RepositoryRef) -> str: ...


class GitHubRemoteLocator:
    """``https://github.com/<owner>/<name>.git`` from a validated repository reference."""

    def __init__(self, base_url: str = "https://github.com/") -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.query:
            raise ValueError("GitHub Git base URL must be an https URL without credentials")
        self._base = base_url.rstrip("/") + "/"

    def url_for(self, repository: RepositoryRef) -> str:
        return (
            f"{self._base}{quote(repository.owner, safe='')}/{quote(repository.name, safe='')}.git"
        )


def _auth_environment(url: str, token: Secret) -> dict[str, str]:
    parts = urlsplit(url)
    prefix = f"{parts.scheme}://{parts.netloc}/"
    basic = base64.b64encode(f"x-access-token:{token.reveal()}".encode()).decode("ascii")
    register_secret(basic)
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"http.{prefix}.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _valid_branch(name: str) -> bool:
    return (
        0 < len(name) <= _REF_NAME_MAX
        and not name.startswith(("-", "/"))
        and name.isprintable()
        and " " not in name
        and run_git(["check-ref-format", f"refs/heads/{name}"], check=False).ok
    )


def require_mirror_git() -> None:
    try:
        version = git_version()
    except GitError:
        raise InfrastructureError("git is not available") from None
    if version[:2] < MINIMUM_MIRROR_GIT_VERSION:
        wanted = ".".join(map(str, MINIMUM_MIRROR_GIT_VERSION))
        found = ".".join(map(str, version))
        raise InfrastructureError(f"the GitHub App needs Git {wanted} or newer (found {found})")


class MirrorManager:
    def __init__(
        self,
        root: Path,
        locator: RemoteLocator,
        *,
        allowed_protocols: Sequence[str] = ("https",),
        fetch_timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    ) -> None:
        require_mirror_git()
        self.root = root
        self._locator = locator
        self._protocols = tuple(allowed_protocols)
        self._fetch_timeout = fetch_timeout
        self._locks: dict[tuple[int, int], threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def path_for(self, installation_id: int, repository_id: int) -> Path:
        return self.root / str(int(installation_id)) / f"{int(repository_id)}.git"

    def _lock(self, installation_id: int, repository_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault((installation_id, repository_id), threading.Lock())

    def _git_options(self) -> list[str]:
        options = ["-c", "protocol.allow=never"]
        for protocol in self._protocols:
            options += ["-c", f"protocol.{protocol}.allow=always"]
        options += [
            "-c", "http.followRedirects=false",
            "-c", "credential.helper=",
            "-c", f"core.hooksPath={os.devnull}",
            "-c", "submodule.recurse=false",
            "-c", "fetch.recurseSubmodules=false",
            "-c", "fetch.writeCommitGraph=false",
            "-c", "gc.auto=0",
            "-c", "maintenance.auto=false",
        ]  # fmt: skip
        return options

    def _init(self, path: Path, url: str) -> None:
        if not (path / "HEAD").is_file():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            run_git(["init", "--quiet", "--bare", "--template=", "--", str(path)])
        for key, value in (
            ("remote.origin.url", url),
            ("remote.origin.promisor", "true"),
            ("remote.origin.partialclonefilter", "blob:none"),
            ("core.hooksPath", os.devnull),
            ("gc.auto", "0"),
            ("maintenance.auto", "false"),
        ):
            run_git(["config", "--end-of-options", key, value], cwd=path)

    def _fetch(
        self, path: Path, refspecs: Sequence[str], env: dict[str, str], *, check: bool = True
    ) -> bool:
        args = [
            *self._git_options(),
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            "--filter=blob:none",
            "origin",
            *refspecs,
        ]
        try:
            result = run_git(
                args, cwd=path, check=False, timeout=self._fetch_timeout, extra_env=env
            )
        except GitError as exc:
            if "timed out" in str(exc):
                raise FetchTimeoutError("fetching commits from GitHub timed out") from None
            raise InfrastructureError(f"git fetch failed: {safe_text(str(exc))}") from None
        if result.ok or not check:
            return result.ok
        lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = safe_text(lines[-1]) if lines else "no details"
        if any(marker in detail for marker in ("not our ref", "couldn't find remote ref")):
            raise ScanError(f"a commit to scan is not available on GitHub ({detail})")
        raise InfrastructureError(f"could not fetch commits from GitHub ({detail})")

    def prepare(
        self,
        installation_id: int,
        repository: RepositoryRef,
        token: Secret | None,
        *,
        required: Sequence[str],
        optional: Sequence[str] = (),
        branches: Sequence[str] = (),
        config_path: str | None = None,
    ) -> Repository:
        """Fetch commits (and config blobs) into the mirror and return it for analysis."""
        for oid in (*required, *optional):
            if not is_git_sha(oid):
                raise ScanError("mirror fetch requires full commit ids")
        if config_path is not None:
            validate_repository_path(config_path)
        url = self._locator.url_for(repository)
        env = dict(MIRROR_GIT_ENV)
        if token is not None:
            env.update(_auth_environment(url, token))
        path = self.path_for(installation_id, repository.id)
        with self._lock(installation_id, repository.id):
            try:
                self._init(path, url)
            except GitCommandError as exc:
                raise InfrastructureError(
                    f"could not prepare mirror: {safe_text(str(exc))}"
                ) from None
            wanted = sorted(set(required))
            if wanted:
                self._fetch(path, [f"+{oid}:refs/commitguard/{oid}" for oid in wanted], env)
            for oid in sorted(set(optional) - set(wanted)):
                self._fetch(path, [f"+{oid}:refs/commitguard/{oid}"], env, check=False)
            tips = []
            for branch in branches:
                if not _valid_branch(branch):
                    continue
                refspec = f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
                if self._fetch(path, [refspec], env, check=False):
                    tips.append(f"refs/remotes/origin/{branch}")
            blobs = self._missing_config_blobs(path, [*wanted, *optional, *tips], config_path)
            if blobs:
                self._fetch(path, blobs, env)
            (path / LAST_USED_MARKER).touch()
        return Repository(root=path, git_dir=path, git_env=MIRROR_GIT_ENV)

    def _missing_config_blobs(
        self, path: Path, revisions: Iterable[str], config_path: str | None
    ) -> list[str]:
        names = [config_path] if config_path else list(CONFIG_FILENAMES)
        missing: set[str] = set()
        for revision in revisions:
            listed = run_git(
                ["ls-tree", "-z", "--end-of-options", revision, "--", *names],
                cwd=path,
                check=False,
                extra_env=MIRROR_GIT_ENV,
            )
            if not listed.ok:
                continue  # optional commit that could not be fetched
            for entry in filter(None, listed.stdout.split(b"\x00")):
                meta = entry.partition(b"\t")[0].decode("ascii", "replace").split()
                if len(meta) == 3 and meta[1] == "blob" and is_git_sha(meta[2]):
                    exists = run_git(
                        ["cat-file", "-e", meta[2]], cwd=path, check=False, extra_env=MIRROR_GIT_ENV
                    )
                    if not exists.ok:
                        missing.add(meta[2])
        return sorted(missing)

    def _remove(self, target: Path) -> None:
        root = self.root.resolve()
        resolved = target.resolve()
        if resolved == root or not resolved.is_relative_to(root) or target.is_symlink():
            raise InfrastructureError("refusing to remove a path outside the mirror directory")
        if resolved.exists():
            shutil.rmtree(resolved, onexc=_clear_read_only_and_retry)

    def remove_repository(self, installation_id: int, repository_id: int) -> None:
        with self._lock(installation_id, repository_id):
            self._remove(self.path_for(installation_id, repository_id))

    def remove_installation(self, installation_id: int) -> None:
        self._remove(self.root / str(int(installation_id)))

    def purge_unused(self, older_than_seconds: float) -> int:
        """Remove mirrors not used for ``older_than_seconds`` (retention)."""
        if not self.root.is_dir():
            return 0
        cutoff = time.time() - older_than_seconds
        removed = 0
        for installation_dir in self.root.iterdir():
            if not installation_dir.name.isdigit() or installation_dir.is_symlink():
                continue
            for mirror in installation_dir.glob("*.git"):
                marker = mirror / LAST_USED_MARKER
                used = marker.stat().st_mtime if marker.exists() else mirror.stat().st_mtime
                if used < cutoff:
                    self._remove(mirror)
                    removed += 1
        return removed
