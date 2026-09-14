"""Shared pytest fixtures for CommitGuard.

* Every test runs with Git isolated from the developer's global/system config
  and from any ``GIT_DIR``-style variables (e.g. when pytest runs inside a hook).
* ``commit_case`` parametrises a test over every YAML commit fixture in
  ``tests/fixtures/commits``.
* ``git_repo`` provides a throwaway repository for integration tests.
"""

import os
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from commitguard.core.context import CommitContext
from commitguard.core.result import Finding
from commitguard.detectors.base import Detector
from commitguard.git.commit import Commit
from commitguard.provenance.author import Identity
from commitguard.rules.loader import load_builtin_rules
from commitguard.rules.matcher import CompiledRules

TESTS_DIR = Path(__file__).parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
COMMIT_FIXTURES_DIR = FIXTURES_DIR / "commits"
POLICY_FIXTURES_DIR = FIXTURES_DIR / "policies"
PROJECT_ROOT = TESTS_DIR.parent

# Variables that redirect Git to a different repository or index.
_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
)


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_dir = TESTS_DIR / "integration"
    for item in items:
        if integration_dir in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.integration)


# --------------------------------------------------------------------------- #
# Environment isolation
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def isolated_git_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    home = tmp_path_factory.mktemp("home")
    overrides = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.com",
        "GIT_COMMITTER_NAME": "Test Committer",
        "GIT_COMMITTER_EMAIL": "committer@example.com",
    }
    with pytest.MonkeyPatch.context() as mp:
        for var in _GIT_LOCATION_VARS:
            mp.delenv(var, raising=False)
        for key, value in overrides.items():
            mp.setenv(key, value)
        yield


# --------------------------------------------------------------------------- #
# Commit fixtures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CommitCase:
    """A commit fixture plus the rules Phase 2 detectors are expected to emit."""

    name: str
    description: str
    commit: Commit
    expected_rules: frozenset[str]


def _identity(data: Mapping[str, str]) -> Identity:
    return Identity(name=data["name"], email=data["email"])


def load_commit_case(path: Path) -> CommitCase:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    author = _identity(data["author"])
    committer = _identity(data.get("committer", data["author"]))
    commit = Commit(author=author, committer=committer, message=data["message"])
    return CommitCase(
        name=path.stem,
        description=data["description"],
        commit=commit,
        expected_rules=frozenset(data["expected"]["rules"]),
    )


def all_commit_cases() -> list[CommitCase]:
    return [load_commit_case(p) for p in sorted(COMMIT_FIXTURES_DIR.glob("*.yaml"))]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "commit_case" in metafunc.fixturenames:
        cases = all_commit_cases()
        metafunc.parametrize("commit_case", cases, ids=[case.name for case in cases])


@pytest.fixture
def commit_cases() -> list[CommitCase]:
    return all_commit_cases()


@pytest.fixture
def human_commit() -> Commit:
    return load_commit_case(COMMIT_FIXTURES_DIR / "normal_human.yaml").commit


HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")


def _make_commit(
    message: str = "feat: implement authentication\n",
    *,
    author: Identity = HUMAN,
    committer: Identity | None = None,
    sha: str | None = None,
) -> Commit:
    """Build an in-memory commit; trailers are derived from ``message``."""
    return Commit(sha=sha, author=author, committer=committer or author, message=message)


def _run_detector(detector: Detector, commit: Commit) -> list[Finding]:
    return list(detector.detect(CommitContext(commit=commit)))


# Helpers are exposed as fixtures because tests use --import-mode=importlib,
# under which conftest.py is not importable as a module.
@pytest.fixture
def make_commit() -> Callable[..., Commit]:
    return _make_commit


@pytest.fixture
def run_detector() -> Callable[[Detector, Commit], list[Finding]]:
    return _run_detector


@pytest.fixture(scope="session")
def rules() -> CompiledRules:
    return load_builtin_rules()


# --------------------------------------------------------------------------- #
# Git repositories
# --------------------------------------------------------------------------- #
class GitRepo:
    """A throwaway Git repository driven directly through the git CLI."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str, env: Mapping[str, str] | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env={**os.environ, **(env or {})},
            capture_output=True,
            check=True,
        )
        return result.stdout.decode("utf-8", errors="replace").strip()

    def commit(
        self,
        message: str,
        *,
        author: Identity | None = None,
        committer: Identity | None = None,
    ) -> str:
        """Create an empty commit with an exact (verbatim) message; return its SHA."""
        env: dict[str, str] = {}
        if author:
            env |= {"GIT_AUTHOR_NAME": author.name, "GIT_AUTHOR_EMAIL": author.email}
        if committer:
            env |= {"GIT_COMMITTER_NAME": committer.name, "GIT_COMMITTER_EMAIL": committer.email}
        message_file = self.path.parent / "COMMIT_MSG_FIXTURE"
        message_file.write_bytes(message.encode("utf-8"))
        self.git(
            "commit",
            "--allow-empty",
            "--no-verify",
            "--cleanup=verbatim",
            "-F",
            str(message_file),
            env=env,
        )
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    """An initialised, empty repository; the CWD is set to its root."""
    path = tmp_path / "repo"
    path.mkdir()
    repo = GitRepo(path)
    repo.git("init", "--quiet", "--initial-branch=main")
    repo.git("config", "commit.gpgsign", "false")
    monkeypatch.chdir(path)
    return repo
