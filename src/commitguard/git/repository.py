"""Repository discovery and read-only repository queries.

Nothing in this module writes to the repository.
"""

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
_COMMIT_FORMAT = "%x00".join(["%H", "%P", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B"])
_COMMIT_FIELD_COUNT = 9


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
        """Read a commit's metadata into a normalised :class:`Commit`.

        Mailmap rewriting, notes, signature display and replace refs are all
        disabled so that the metadata inspected is exactly what the commit
        object records.
        """
        sha = self.resolve_commit(revision)
        result = run_git(
            [
                "-c",
                "log.showSignature=false",
                "-c",
                "i18n.logOutputEncoding=UTF-8",
                "log",
                "-1",
                "--no-use-mailmap",
                "--no-notes",
                "--no-color",
                f"--pretty=format:{_COMMIT_FORMAT}",
                "--end-of-options",
                sha,
                "--",
            ],
            cwd=self.root,
        )
        return _parse_commit_record(result.stdout, expected_sha=sha)

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
    """Parse the NUL-separated output of :data:`_COMMIT_FORMAT` defensively."""
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
