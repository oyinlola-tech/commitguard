"""Repository discovery and read-only repository queries.

Nothing in this module writes to the repository.
"""

import secrets
from collections.abc import Sequence
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
    validate_revision,
)

# Fields are NUL-separated; the free-form message (%B) comes last so that any
# NUL bytes inside it cannot shift the positions of the structured fields.
_COMMIT_FIELDS = "%x00".join(["%H", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B"])
_COMMIT_FIELD_COUNT = 9
# Commits are read in batches; records are separated by a random per-call
# boundary that commit messages cannot predict.
_READ_BATCH_SIZE = 256


class Repository:
    """A discovered Git repository with a work tree."""

    def __init__(self, root: Path, git_dir: Path) -> None:
        self.root = root
        self.git_dir = git_dir

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

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def resolve_commit(self, revision: str) -> str:
        """Resolve ``revision`` to a full commit object ID."""
        validate_revision(revision)
        result = run_git(
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
            cwd=self.root,
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
        result = run_git(
            [
                "rev-list",
                f"--max-count={max_count + 1}",
                "--end-of-options",
                revision_range,
                "--",
            ],
            cwd=self.root,
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

    def _read_batch(self, shas: Sequence[str]) -> list[Commit]:
        boundary = secrets.token_hex(16)
        result = run_git(
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
            cwd=self.root,
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
        result = run_git(["var", variable], cwd=self.root, check=False)
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

    def config_get(self, key: str) -> str | None:
        """Return a Git configuration value, or None if it is unset."""
        validate_git_config_key(key)
        result = run_git(["config", "--get", "--end-of-options", key], cwd=self.root, check=False)
        if result.returncode == 1:  # key not set
            return None
        if not result.ok:
            raise GitCommandError(result.args, result.returncode, "git config failed")
        return result.stdout.decode("utf-8", errors="replace").rstrip("\n")

    def hooks_dir(self) -> Path:
        """Return the effective hooks directory (honours ``core.hooksPath``)."""
        result = run_git(
            ["rev-parse", "--path-format=absolute", "--git-path", "hooks"], cwd=self.root
        )
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
