"""A realistic, offline model of the GitHub side of CommitGuard enforcement.

* ``hub``        - a bare repository standing in for the GitHub repository;
* ``dev``        - a developer clone that pushes (with --no-verify: no local hooks);
* ``ci_clone``   - what ``actions/checkout`` with ``fetch-depth: 0`` produces:
                   a full clone, optionally positioned on GitHub's synthetic
                   pull request merge commit;
* ``run_ci``     - runs ``python -P -m commitguard ci github`` as a separate
                   process with a GitHub event payload, like the Action does.
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ZERO = "0" * 40
BLOCK_CONFIG = "version: 1\npolicies:\n  ai_coauthor:\n    enabled: true\n    action: block\n"
AI = "feat: add payment service\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"


def git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


@dataclass
class Clone:
    path: Path

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        return git(self.path, *args, env=env)

    def commit(
        self, message: str, *, files: dict[str, str] | None = None, author: str | None = None
    ) -> str:
        for name, content in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            self.git("add", "--", name)
        msg = self.path.parent / f"MSG-{self.path.name}"
        msg.write_bytes(message.encode("utf-8"))
        args = ["commit", "--allow-empty", "--no-verify", "--cleanup=verbatim", "-F", str(msg)]
        if author:
            args.append(f"--author={author}")
        self.git(*args)
        return self.git("rev-parse", "HEAD")

    def push(self, *args: str) -> None:
        self.git("push", "--no-verify", "--quiet", "origin", *args)


@dataclass
class Hub:
    """The GitHub repository (bare) plus a developer clone."""

    bare: Path
    dev: Clone
    tmp: Path

    def ci_clone(
        self, *, checkout: str | None = None, name: str = "ci", depth: int | None = None
    ) -> Clone:
        path = self.tmp / name
        args = ["clone", "--quiet", "--no-local"]
        if depth:
            args += ["--depth", str(depth)]
        git(self.tmp, *args, self.bare.as_uri() if depth else str(self.bare), str(path))
        clone = Clone(path)
        if checkout:
            clone.git("checkout", "--quiet", "--detach", checkout)
        return clone

    def synthetic_merge_checkout(self, clone: Clone, base: str, head: str) -> str:
        """Create and check out a merge commit like refs/pull/N/merge."""
        clone.git("checkout", "--quiet", "--detach", base)
        clone.git(
            "merge",
            "--quiet",
            "--no-ff",
            "--no-verify",
            "-m",
            f"Merge {head} into {base}",
            head,
            env={
                "GIT_AUTHOR_NAME": "GitHub",
                "GIT_AUTHOR_EMAIL": "noreply@github.com",
                "GIT_COMMITTER_NAME": "GitHub",
                "GIT_COMMITTER_EMAIL": "noreply@github.com",
            },
        )
        return clone.git("rev-parse", "HEAD")


@pytest.fixture
def hub(tmp_path: Path) -> Hub:
    bare = tmp_path / "github.git"
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(bare)], check=True
    )
    dev_path = tmp_path / "dev"
    subprocess.run(
        ["git", "clone", "--quiet", str(bare), str(dev_path)], check=True, capture_output=True
    )
    dev = Clone(dev_path)
    dev.git("config", "commit.gpgsign", "false")
    dev.git("checkout", "--quiet", "-B", "main")
    dev.commit("chore: initial\n", files={".commitguard.yaml": BLOCK_CONFIG, "README.md": "hi\n"})
    dev.push("main")
    return Hub(bare=bare, dev=dev, tmp=tmp_path)


def pr_event(base: str, head: str, *, number: int = 7, fork: bool = False) -> dict[str, Any]:
    return {
        "action": "synchronize",
        "number": number,
        "pull_request": {
            "number": number,
            "base": {"sha": base, "ref": "main", "repo": {"full_name": "octo/project"}},
            "head": {
                "sha": head,
                "ref": "feature",
                "repo": {"full_name": "stranger/project" if fork else "octo/project"},
            },
        },
        "repository": {"full_name": "octo/project", "default_branch": "main"},
    }


def push_event(before: str, after: str, ref: str = "refs/heads/main") -> dict[str, Any]:
    return {
        "ref": ref,
        "before": before,
        "after": after,
        "deleted": after == ZERO,
        "created": before == ZERO,
        "forced": False,
        "repository": {"full_name": "octo/project", "default_branch": "main"},
    }


@dataclass
class CIResult:
    returncode: int
    stdout: str
    stderr: str
    summary: str
    outputs: dict[str, str]

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def json(self) -> Any:
        return json.loads(self.stdout)


def _clean_env() -> dict[str, str]:
    """Environment without tokens or secrets, as for a fork pull request."""
    blocked = ("TOKEN", "SECRET", "PASSWORD", "GH_", "GITHUB_")
    return {k: v for k, v in os.environ.items() if not any(b in k.upper() for b in blocked)}


def run_ci_process(
    clone: Clone,
    event_name: str,
    payload: Any,
    *args: str,
    actions: bool = True,
    extra_env: dict[str, str] | None = None,
    raw_payload: str | None = None,
) -> CIResult:
    work = clone.path.parent
    event_file = work / f"event-{event_name}.json"
    event_file.write_text(raw_payload if raw_payload is not None else json.dumps(payload))
    summary = work / "step-summary.md"
    outputs = work / "github-output.txt"
    for f in (summary, outputs):
        f.write_text("")
    env = _clean_env()
    env.update(
        {
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_EVENT_PATH": str(event_file),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(outputs),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if actions:
        env["GITHUB_ACTIONS"] = "true"
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, "-P", "-m", "commitguard", "ci", "github", *args],
        cwd=clone.path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    parsed = dict(line.split("=", 1) for line in outputs.read_text().splitlines() if "=" in line)
    return CIResult(result.returncode, result.stdout, result.stderr, summary.read_text(), parsed)


@pytest.fixture
def run_ci():  # type: ignore[no-untyped-def]
    return run_ci_process


# conftest.py is not importable under --import-mode=importlib: expose helpers.
@pytest.fixture
def gh():  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    return SimpleNamespace(
        pr_event=pr_event, push_event=push_event, ZERO=ZERO, AI=AI, BLOCK_CONFIG=BLOCK_CONFIG
    )
