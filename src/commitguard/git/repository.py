"""Repository discovery and read-only repository queries.

Nothing in this module writes to the repository.
"""

import secrets
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from commitguard.exceptions.base import UnsafeInputError
from commitguard.exceptions.git import (
    GitCommandError,
    GitError,
    MalformedGitOutputError,
    NotAGitRepositoryError,
)
from commitguard.git.commands import run_git
from commitguard.git.commit import Commit
from commitguard.provenance.author import Identity
from commitguard.security.validation import (
    is_git_sha,
    validate_git_config_key,
    validate_repository_path,
    validate_revision,
)
from commitguard.utils.subprocess import CommandResult

# Fields are NUL-separated; the free-form message (%B) comes last so that any
# NUL bytes inside it cannot shift the positions of the structured fields.
_COMMIT_FIELDS = "%x00".join(["%H", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B"])
_COMMIT_FIELD_COUNT = 9
# Commits are read in batches; records are separated by a random per-call
# boundary that commit messages cannot predict.
_READ_BATCH_SIZE = 256


class Repository:
    """A discovered Git repository with a work tree."""

    def __init__(
        self, root: Path, git_dir: Path, *, git_env: Mapping[str, str] | None = None
    ) -> None:
        self.root = root
        self.git_dir = git_dir
        # Extra environment for every git invocation on this repository (e.g. a
        # server-side mirror disables lazy object fetching). Never holds secrets.
        self._git_env = dict(git_env) if git_env else None

    def __repr__(self) -> str:
        return f"Repository(root={self.root!s})"

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    @classmethod
    def discover(cls, path: Path | None = None) -> "Repository":
        """Locate the repository containing ``path`` (default: CWD).

        Raises :class:`NotAGitRepositoryError` if ``path`` is not inside a work
        tree. Bare repositories are not supported yet.
        """
        start = (path or Path.cwd()).resolve()
        try:
            result = run_git(
                [
                    "rev-parse",
                    "--path-format=absolute",
                    "--show-toplevel",
                    "--absolute-git-dir",
                ],
                cwd=start,
            )
        except GitCommandError as exc:
            raise NotAGitRepositoryError(f"not inside a Git work tree: {start}") from exc

        lines = result.stdout.decode("utf-8", errors="surrogateescape").splitlines()
        if len(lines) != 2 or not all(lines):
            raise NotAGitRepositoryError(f"not inside a Git work tree: {start}")
        return cls(root=Path(lines[0]), git_dir=Path(lines[1]))

    def _git(
        self,
        args: Sequence[str],
        *,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        return run_git(
            args, cwd=self.root, check=check, input_bytes=input_bytes, extra_env=self._git_env
        )

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def resolve_commit(self, revision: str) -> str:
        """Resolve ``revision`` to a full commit object ID."""
        validate_revision(revision)
        result = self._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
            check=False,
        )
        sha = result.stdout.decode("ascii", errors="replace").strip()
        if not result.ok or not is_git_sha(sha):
            raise GitError("revision does not resolve to a commit")
        return sha

    def read_commit(self, revision: str = "HEAD") -> Commit:
        """Read one commit's metadata into a normalised :class:`Commit`."""
        return self.read_commits([self.resolve_commit(revision)])[0]

    def list_commits(self, revision_range: str, *, max_count: int) -> list[str]:
        """Return commit IDs selected by ``revision_range``, oldest first.

        A plain revision (``HEAD``, a SHA, a branch) selects that single commit.
        A range containing ``..`` (``origin/main..HEAD``) selects every commit in
        the range, as ``git rev-list`` does. More than ``max_count`` commits is an
        error rather than a silent truncation.
        """
        validate_revision(revision_range)
        if ".." not in revision_range:
            return [self.resolve_commit(revision_range)]
        result = self._git(
            [
                "rev-list",
                f"--max-count={max_count + 1}",
                "--end-of-options",
                revision_range,
                "--",
            ],
            check=False,
        )
        if not result.ok:
            raise GitError("revision range could not be resolved")
        shas = result.stdout.decode("ascii", errors="replace").split()
        if not all(is_git_sha(sha) for sha in shas):
            raise MalformedGitOutputError("unexpected rev-list output")
        if len(shas) > max_count:
            raise GitError(f"revision range selects more than {max_count} commits")
        shas.reverse()
        return shas

    def read_commits(self, shas: Sequence[str]) -> list[Commit]:
        """Read metadata for full commit IDs, preserving order.

        Mailmap rewriting, notes, signature display and replace refs are all
        disabled so that the metadata inspected is exactly what the commit
        objects record.
        """
        for sha in shas:
            if not is_git_sha(sha):
                raise UnsafeInputError("read_commits requires full commit ids")
        commits: list[Commit] = []
        for start in range(0, len(shas), _READ_BATCH_SIZE):
            commits.extend(self._read_batch(shas[start : start + _READ_BATCH_SIZE]))
        return commits

    def iter_commits(self, shas: Sequence[str]) -> Iterator[Commit]:
        """Like :meth:`read_commits`, but reads one batch at a time (bounded memory)."""
        for sha in shas:
            if not is_git_sha(sha):
                raise UnsafeInputError("iter_commits requires full commit ids")
        for start in range(0, len(shas), _READ_BATCH_SIZE):
            yield from self._read_batch(shas[start : start + _READ_BATCH_SIZE])

    def _read_batch(self, shas: Sequence[str]) -> list[Commit]:
        boundary = secrets.token_hex(16)
        result = self._git(
            [
                "-c",
                "log.showSignature=false",
                "-c",
                "i18n.logOutputEncoding=UTF-8",
                "log",
                "--no-walk=unsorted",
                "--no-use-mailmap",
                "--no-notes",
                "--no-color",
                f"--pretty=format:%x00{boundary}%x00{_COMMIT_FIELDS}",
                "--end-of-options",
                *shas,
                "--",
            ],
        )
        records = result.stdout.split(f"\x00{boundary}\x00".encode("ascii"))
        if records[0].strip():
            raise MalformedGitOutputError("unexpected data before first commit record")
        records = records[1:]
        if len(records) != len(shas):
            raise MalformedGitOutputError("commit record count does not match request")
        return [
            # git separates records with a newline, which is not part of the message
            _parse_commit_record(
                record.removesuffix(b"\n") if index < len(records) - 1 else record, expected_sha=sha
            )
            for index, (record, sha) in enumerate(zip(records, shas, strict=True))
        ]

    def pending_identities(self) -> tuple[Identity, Identity]:
        """Author and committer Git would use for a new commit (``git var``)."""
        return self._git_var_identity("GIT_AUTHOR_IDENT"), self._git_var_identity(
            "GIT_COMMITTER_IDENT"
        )

    def _git_var_identity(self, variable: str) -> Identity:
        result = self._git(["var", variable], check=False)
        if not result.ok:
            raise GitError(f"git could not determine {variable} (is user.name/user.email set?)")
        line = result.stdout.decode("utf-8", errors="replace").rstrip("\n")
        # "Name <email> 1700000000 +0100"
        ident, _, _timezone = line.rpartition(" ")
        ident, _, _timestamp = ident.rpartition(" ")
        open_index, close_index = ident.rfind("<"), ident.rfind(">")
        if open_index < 0 or close_index < open_index or close_index != len(ident) - 1:
            raise MalformedGitOutputError(f"unexpected {variable} format")
        return Identity(name=ident[:open_index].strip(), email=ident[open_index + 1 : close_index])

    @property
    def common_dir(self) -> Path:
        """The Git directory shared by all worktrees (where hooks normally live)."""
        result = self._git(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        return Path(result.stdout.decode("utf-8", errors="surrogateescape").strip())

    def peel_to_commit(self, oid: str) -> str | None:
        """Return the commit an object (commit or annotated tag) refers to.

        Returns None if the object does not exist locally or peels to a
        non-commit object (e.g. a tag pointing at a tree or blob).
        """
        if not is_git_sha(oid):
            raise UnsafeInputError("peel_to_commit requires a full object id")
        result = self._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{oid}^{{commit}}"],
            check=False,
        )
        sha = result.stdout.decode("ascii", errors="replace").strip()
        return sha if result.ok and is_git_sha(sha) else None

    def object_exists(self, oid: str) -> bool:
        if not is_git_sha(oid):
            raise UnsafeInputError("object_exists requires a full object id")
        return self._git(["cat-file", "-e", oid], check=False).ok

    def has_commit(self, oid: str) -> bool:
        return is_git_sha(oid) and self.peel_to_commit(oid) == oid

    def remote_exists(self, name: str) -> bool:
        if not _is_simple_remote_name(name):
            return False
        return self.config_get(f"remote.{name}.url") is not None

    def remote_tracking_tips(self, remote: str) -> list[str]:
        """Commit IDs at the tips of ``refs/remotes/<remote>/*`` (what the remote had)."""
        if not self.remote_exists(remote):
            return []
        # One call: object type/name and, for tags, the peeled type/name.
        result = self._git(
            [
                "for-each-ref",
                "--format=%(objecttype) %(objectname) %(*objecttype) %(*objectname)",
                "--",
                f"refs/remotes/{remote}/",
            ],
        )
        tips = []
        for line in result.stdout.decode("ascii", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "commit" and is_git_sha(fields[1]):
                tips.append(fields[1])
            elif len(fields) == 4 and fields[2] == "commit" and is_git_sha(fields[3]):
                tips.append(fields[3])
        return sorted(set(tips))

    def rev_list(
        self,
        include: Sequence[str],
        exclude: Sequence[str] = (),
        *,
        max_count: int,
    ) -> list[str]:
        """Commits reachable from ``include`` but not from ``exclude``, oldest first.

        All inputs must be full object IDs; they are passed on stdin (no argument
        length limits, no option parsing). More than ``max_count`` results is an
        error rather than a silent truncation.
        """
        for oid in (*include, *exclude):
            if not is_git_sha(oid):
                raise UnsafeInputError("rev_list requires full object ids")
        if not include:
            return []
        stdin = "".join(f"{oid}\n" for oid in include) + "".join(f"^{oid}\n" for oid in exclude)
        result = self._git(
            ["rev-list", f"--max-count={max_count + 1}", "--stdin"],
            input_bytes=stdin.encode("ascii"),
        )
        shas = result.stdout.decode("ascii", errors="replace").split()
        if not all(is_git_sha(sha) for sha in shas):
            raise MalformedGitOutputError("unexpected rev-list output")
        if len(shas) > max_count:
            raise GitError(f"more than {max_count} commits would need to be analysed")
        shas.reverse()
        return shas

    def cleanup_message(self, message: str) -> str:
        """Approximate the cleanup ``git commit`` applies before storing a message.

        A commit-msg hook cannot see whether an editor was used or which
        ``--cleanup`` option was given, so this errs towards *keeping* text:

        * comment lines are stripped only when ``commit.cleanup=strip`` is
          configured (with ``-m``/``-F``, Git's default keeps ``#`` lines, so
          stripping them would hide attribution that really gets stored);
        * everything from Git's scissors line is dropped unless the mode is
          ``verbatim``/``whitespace`` (the diff ``git commit -v`` appends there
          is never part of the message);
        * whitespace is normalised with ``git stripspace`` unless ``verbatim``.

        pre-push analyses the real commit objects and is the authoritative check.
        """
        mode = (self.config_get("commit.cleanup") or "default").lower()
        if mode == "verbatim":
            return message
        if mode in ("default", "strip", "scissors"):
            message = _cut_at_scissors(message)
        args = ["stripspace"]
        if mode == "strip":
            args.append("--strip-comments")
        result = self._git(args, input_bytes=message.encode("utf-8", "surrogatepass"))
        return result.stdout.decode("utf-8", errors="replace")

    def ref_commit(self, refname: str) -> str | None:
        """The commit a fully qualified ref (``refs/...``) points to, if it exists."""
        if not refname.startswith("refs/") or any(ord(c) < 0x20 for c in refname):
            raise UnsafeInputError("ref_commit requires a fully qualified ref name")
        result = self._git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{refname}^{{commit}}"],
            check=False,
        )
        sha = result.stdout.decode("ascii", errors="replace").strip()
        return sha if result.ok and is_git_sha(sha) else None

    def read_blob_at(self, commit: str, path: str, *, max_bytes: int) -> bytes | None:
        """Read a regular file from a commit's tree (not from the work tree).

        Returns None if the path does not exist at that commit. Raises
        :class:`UnsafeInputError` for non-regular entries (symlinks,
        submodules, directories) or files larger than ``max_bytes``.
        """
        if not is_git_sha(commit):
            raise UnsafeInputError("read_blob_at requires a full commit id")
        validate_repository_path(path)
        result = self._git(["ls-tree", "-z", "--end-of-options", commit, "--", path])
        entries = [e for e in result.stdout.split(b"\x00") if e]
        match = None
        for entry in entries:
            meta, _, name = entry.partition(b"\t")
            if name.decode("utf-8", errors="surrogateescape") == path:
                match = meta.decode("ascii", errors="replace").split()
        if match is None:
            return None
        if len(match) != 3 or match[1] != "blob" or match[0] not in ("100644", "100755"):
            raise UnsafeInputError(f"{path} at {commit[:12]} is not a regular file")
        oid = match[2]
        if not is_git_sha(oid):
            raise MalformedGitOutputError("unexpected ls-tree output")
        size = self._git(["cat-file", "-s", oid]).stdout.decode("ascii").strip()
        if not size.isdigit() or int(size) > max_bytes:
            raise UnsafeInputError(f"{path} at {commit[:12]} is larger than {max_bytes} bytes")
        return self._git(["cat-file", "blob", oid]).stdout

    def config_get(self, key: str) -> str | None:
        """Return a Git configuration value, or None if it is unset."""
        validate_git_config_key(key)
        result = self._git(["config", "--get", "--end-of-options", key], check=False)
        if result.returncode == 1:  # key not set
            return None
        if not result.ok:
            raise GitCommandError(result.args, result.returncode, "git config failed")
        return result.stdout.decode("utf-8", errors="replace").rstrip("\n")

    def hooks_dir(self) -> Path:
        """Return the effective hooks directory (honours ``core.hooksPath``)."""
        result = self._git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"])
        return Path(result.stdout.decode("utf-8", errors="surrogateescape").strip())


def _parse_commit_record(raw: bytes, *, expected_sha: str) -> Commit:
    """Parse the NUL-separated output of :data:`_COMMIT_FIELDS` defensively."""
    fields = raw.decode("utf-8", errors="replace").split("\x00", _COMMIT_FIELD_COUNT - 1)
    if len(fields) != _COMMIT_FIELD_COUNT:
        raise MalformedGitOutputError("unexpected commit record shape")

    sha, parents, a_name, a_email, a_date, c_name, c_email, c_date, message = fields
    if sha != expected_sha:
        raise MalformedGitOutputError("commit record does not match requested object")

    try:
        return Commit(
            sha=sha,
            parents=tuple(parents.split()),
            author=Identity(name=a_name, email=a_email),
            committer=Identity(name=c_name, email=c_email),
            authored_at=datetime.fromisoformat(a_date),
            committed_at=datetime.fromisoformat(c_date),
            message=message,
        )
    except (ValueError, UnsafeInputError, ValidationError) as exc:
        raise MalformedGitOutputError(f"invalid commit metadata: {type(exc).__name__}") from exc


_SCISSORS = "------------------------ >8 ------------------------"


def _cut_at_scissors(message: str) -> str:
    lines = message.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.endswith(_SCISSORS) and len(stripped) <= len(_SCISSORS) + 4:
            return "\n".join(lines[:index]) + ("\n" if index else "")
    return message


def _is_simple_remote_name(name: str) -> bool:
    """A configured remote name usable in ref patterns (no globs, paths or URLs)."""
    return (
        0 < len(name) <= 200
        and not name.startswith(("-", "."))
        and all(ch.isalnum() or ch in "._-" for ch in name)
    )
