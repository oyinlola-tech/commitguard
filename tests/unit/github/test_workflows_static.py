"""Static security checks for shipped workflows, the Action and the workflow template."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from commitguard.exceptions.base import UnsafeInputError
from commitguard.github.workflow import (
    CHECK_NAME,
    JOB_ID,
    WorkflowIssueLevel,
    inspect_workflow_text,
    render_workflow,
)

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
ACTION = ROOT / "action.yml"
PINNED = re.compile(r"\A[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}\Z")
TEMPLATE = render_workflow("octo-org/commitguard", "0123456789abcdef0123456789abcdef01234567")


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(document: Any) -> list[dict[str, Any]]:
    if "runs" in document:
        return list(document["runs"]["steps"])
    return [step for job in document["jobs"].values() for step in job.get("steps", [])]


@pytest.mark.parametrize("path", [*WORKFLOWS, ACTION], ids=lambda p: p.name)
def test_all_third_party_actions_are_pinned_to_commit_shas(path: Path) -> None:
    for step in _steps(_load(path)):
        uses = step.get("uses")
        if uses and not uses.startswith("./"):
            assert PINNED.match(uses), f"{path.name}: {uses} is not pinned to a commit SHA"


@pytest.mark.parametrize(
    "text", [(ROOT / ".github/workflows/commitguard.yml").read_text(), ACTION.read_text(), TEMPLATE]
)
def test_enforcement_run_scripts_never_interpolate_expressions(text: str) -> None:
    """Untrusted event data must reach scripts through env, never ${{ }} in `run:`."""
    for step in _steps(yaml.safe_load(text)):
        assert "${{" not in step.get("run", ""), step.get("name")


@pytest.mark.parametrize(
    "document",
    [_load(ROOT / ".github/workflows/commitguard.yml"), yaml.safe_load(TEMPLATE)],
    ids=["repository-workflow", "template"],
)
def test_enforcement_workflows_are_least_privilege(document: Any) -> None:
    assert document["permissions"] == {"contents": "read"}
    triggers = document.get("on", document.get(True))
    assert set(triggers) == {"pull_request", "merge_group", "push"}
    assert "pull_request_target" not in triggers
    assert "concurrency" not in document
    job = document["jobs"][JOB_ID]
    assert job["name"] == CHECK_NAME
    assert "permissions" not in job
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False


@pytest.mark.parametrize("path", [*WORKFLOWS, ACTION], ids=lambda p: p.name)
def test_no_secrets_and_no_write_permissions(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\$\{\{[^}]*\bsecrets\.", text)
    assert "write-all" not in text
    assert ": write" not in text


def test_action_inputs_are_only_supported_options() -> None:
    action = _load(ACTION)
    assert set(action["inputs"]) == {"config", "fail-on", "max-commits", "python-version"}
    run = action["runs"]["steps"][2]["run"]
    assert "--fail-on" in run
    assert "--max-commits" in run
    assert "--config" in run
    assert "-P -m commitguard" in run
    install = action["runs"]["steps"][1]["run"]
    assert "--require-hashes" in install
    assert "--no-build-isolation" in install


def test_repository_workflow_installs_commitguard_from_trusted_commit() -> None:
    steps = _load(ROOT / ".github/workflows/commitguard.yml")["jobs"][JOB_ID]["steps"]
    install = next(s for s in steps if s.get("id") == "install")["run"]
    assert "git worktree add --detach" in install
    assert "--require-hashes" in install
    assert "pip install" in install
    assert "pip install -e" not in install


def test_ci_workflow_installs_checked_out_source_not_pypi() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert 'pip install -e ".[dev]"' in ci
    assert "pip install commitguard" not in ci


def test_template_passes_inspection_cleanly() -> None:
    inspection = inspect_workflow_text(Path("commitguard.yml"), TEMPLATE)
    assert inspection is not None
    assert inspection.check_names == (CHECK_NAME,)
    assert [i for i in inspection.issues if i.level is not WorkflowIssueLevel.OK] == []


@pytest.mark.parametrize(
    ("repository", "ref"),
    [
        ("octo-org/commitguard", "main"),
        ("octo-org/commitguard", "v1.0.0"),
        ("octo-org/commitguard", "0123456"),
        ("octo-org", "0123456789abcdef0123456789abcdef01234567"),
        ("octo/commitguard\n- run: evil", "0123456789abcdef0123456789abcdef01234567"),
    ],
)
def test_template_requires_owner_repo_and_full_sha(repository: str, ref: str) -> None:
    with pytest.raises(UnsafeInputError):
        render_workflow(repository, ref)


def _job(steps: str, *, on: str = "  pull_request:\n", top: str = "permissions:\n  contents: read\n") -> str:
    return (
        f"name: X\non:\n{on}{top}jobs:\n  guard:\n    runs-on: ubuntu-latest\n    steps:\n{steps}"
    )


GOOD_STEPS = (
    "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
    "        with:\n          fetch-depth: 0\n"
    "      - run: commitguard ci github\n"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_job(GOOD_STEPS, on="  pull_request_target:\n"), "pull_request_target"),
        (_job(GOOD_STEPS, top="permissions: write-all\n"), "write-all"),
        (_job(GOOD_STEPS, top="permissions:\n  contents: write\n"), "write access"),
        (_job(GOOD_STEPS, top=""), "no permissions block"),
        (_job(GOOD_STEPS.replace("fetch-depth: 0", "fetch-depth: 1")), "fetch-depth: 0"),
        (_job(GOOD_STEPS.replace("@11bd71901bbe5b1630ceea73d27597364c9af683", "@v4")), "not pinned"),
        (_job(GOOD_STEPS, on="  push:\n"), "does not run on pull_request"),
        (
            _job(GOOD_STEPS, top="permissions:\n  contents: read\nconcurrency:\n  group: x\n  cancel-in-progress: true\n"),
            "cancel-in-progress",
        ),
        (_job(GOOD_STEPS + "        env:\n          T: ${{ secrets.TOKEN }}\n"), "references secrets"),
    ],
)
def test_inspection_flags_unsafe_workflows(text: str, expected: str) -> None:
    inspection = inspect_workflow_text(Path("w.yml"), text)
    assert inspection is not None
    assert any(expected in issue.message for issue in inspection.issues), inspection.issues


def test_workflows_without_commitguard_are_ignored() -> None:
    assert inspect_workflow_text(Path("w.yml"), _job("      - run: echo hi\n")) is None
