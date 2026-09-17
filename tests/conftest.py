"""Shared pytest fixtures for CommitGuard.

* Every test runs with Git isolated from the developer's global/system config
  and from any ``GIT_DIR``-style variables (e.g. when pytest runs inside a hook).
* ``commit_case`` parametrises a test over every YAML commit fixture in
  ``tests/fixtures/commits``.
* ``git_repo`` provides a throwaway repository for integration tests.
* ``security`` marks the security regression suite (``pytest -m security``,
  ``commitguard test security``): everything under the paths in
  :data:`SECURITY_TEST_PATHS`.
* ``observe`` records a security experiment's observed outcome as evidence when
  ``COMMITGUARD_EVIDENCE_DIR`` is set (``commitguard reproduce security``).
"""

import json
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


# Tests that make up the security regression suite: parsers, fuzzing and properties,
# bypass and tampering experiments, webhook and GitHub integration security,
# authorization and tenant isolation. Directories include everything below them.
SECURITY_TEST_PATHS = tuple(
    TESTS_DIR / relative
    for relative in (
        "security",
        "unit/security",
        "unit/provenance/test_trailers.py",
        "unit/provenance/test_identity_parsing.py",
        "unit/github/app/test_webhooks.py",
        "unit/github/app/test_secrets_and_logging.py",
        "integration/github/security",
        "integration/github/test_ci_security.py",
        "integration/github/test_defense_in_depth.py",
        "integration/github/app/security",
        "integration/github/app/test_app_security.py",
        "integration/github/app/test_app_merge_queue_reruns.py",
        "integration/github/app/dashboard/test_dashboard_security.py",
        "integration/github/app/dashboard/test_dashboard_authorization.py",
        "integration/github/app/dashboard/test_governance_isolation.py",
    )
)


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #
def _is_security_test(path: Path) -> bool:
    return any(path == target or target in path.parents for target in SECURITY_TEST_PATHS)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_dir = TESTS_DIR / "integration"
    for item in items:
        path = Path(str(item.fspath))
        if integration_dir in path.parents:
            item.add_marker(pytest.mark.integration)
        if _is_security_test(path):
            item.add_marker(pytest.mark.security)


# --------------------------------------------------------------------------- #
# Security experiment evidence
# --------------------------------------------------------------------------- #
EXPERIMENT_FIELDS = (
    "experiment",
    "area",
    "attack",
    "expected",
    "observed",
    "consequence",
    "mitigation",
    "limitation",
)


@pytest.fixture(scope="session", autouse=True)
def _fresh_experiment_evidence() -> None:
    """Start each run with an empty experiments file.

    Records are appended as tests run, so without this a second run would double
    the evidence rather than replace it, and the security report would count each
    experiment twice.
    """
    directory = os.environ.get("COMMITGUARD_EVIDENCE_DIR")
    if directory:
        target = Path(directory) / "experiments.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")


@pytest.fixture
def observe(request: pytest.FixtureRequest) -> Callable[..., None]:
    """Record what a security experiment actually observed.

    ``observed`` must be built from values the test measured. ``outcome`` is one
    of ``prevented`` (the attack did not achieve its goal), ``detected`` (it got
    through a layer but a later layer reported or blocked it), ``bypassed`` (the
    attack succeeded against this layer - a documented limitation) or
    ``not_applicable``.
    """

    def record(*, outcome: str, **fields: str) -> None:
        missing = [name for name in EXPERIMENT_FIELDS if not fields.get(name)]
        if missing:
            raise AssertionError(f"experiment record is missing: {', '.join(missing)}")
        if outcome not in ("prevented", "detected", "bypassed", "not_applicable"):
            raise AssertionError(f"unknown outcome {outcome!r}")
        directory = os.environ.get("COMMITGUARD_EVIDENCE_DIR")
        if not directory:
            return
        target = Path(directory) / "experiments.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {"test": request.node.nodeid, "outcome": outcome, **fields}
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")

    return record


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

    def run(
        self, *args: str, env: Mapping[str, str] | None = None, input_text: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run git without raising (for operations that hooks may block)."""
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            input=input_text,
            check=False,
            encoding="utf-8",
            errors="replace",
        )

    def head(self) -> str:
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


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """An empty bare repository to push to."""
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(path)], check=True
    )
    return path


def remote_refs(remote: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)"],
        cwd=remote,
        capture_output=True,
        text=True,
        check=True,
    )
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, oid = line.rsplit(" ", 1)
        refs[name] = oid
    return refs


@pytest.fixture
def get_remote_refs() -> Callable[[Path], dict[str, str]]:
    return remote_refs
