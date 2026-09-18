"""The examples must keep working, and keep saying what is true.

Documentation drifts silently; these tests make the examples fail loudly instead.
Every configuration is parsed by the real loader, every sample commit is decided
by the real engine, and the workflow example is checked by the same inspector
`commitguard doctor` uses.
"""

import json
from pathlib import Path

import pytest

from commitguard.config.loader import parse_config
from commitguard.core.decision import Action
from commitguard.git.commit import Commit
from commitguard.github.workflow import CHECK_NAME, WorkflowIssueLevel, inspect_workflow_text
from commitguard.policies.loader import build_policy_set
from commitguard.policies.mandatory import apply_mandatory_policies
from commitguard.provenance.author import Identity
from commitguard.services.analysis import Analyzer

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
DIRECTORIES = sorted(path for path in EXAMPLES.iterdir() if path.is_dir())
HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")


def test_every_example_has_a_readme() -> None:
    assert DIRECTORIES, "no examples found"
    for directory in DIRECTORIES:
        readme = directory / "README.md"
        assert readme.is_file(), f"{directory.name} has no README.md"
        text = readme.read_text(encoding="utf-8")
        assert "What this shows" in text, f"{directory.name} does not say what it shows"


@pytest.mark.parametrize(
    "config_file", sorted(EXAMPLES.rglob("*.yaml")), ids=lambda path: path.name
)
def test_example_configurations_are_valid(config_file: Path) -> None:
    config = parse_config(config_file.read_text(encoding="utf-8"), path=config_file)
    assert config.version == 1
    build_policy_set(config)


def test_the_basic_example_decides_as_documented() -> None:
    """Each sample commit must produce the decision its README and expected.json state."""
    directory = EXAMPLES / "basic"
    expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    config = parse_config((directory / expected["config"]).read_text(encoding="utf-8"))
    analyzer = Analyzer.create(build_policy_set(config))
    readme = (directory / "README.md").read_text(encoding="utf-8")
    for case in expected["commits"]:
        message = (directory / case["file"]).read_text(encoding="utf-8")
        report = analyzer.analyze(Commit(author=HUMAN, committer=HUMAN, message=message))
        assert report.action is Action(case["expected"]), (
            f"{case['file']} produced {report.action.value}, "
            f"but the example documents {case['expected']}"
        )
        # The README table must agree with expected.json.
        name = Path(case["file"]).name
        row = next((line for line in readme.splitlines() if f"`commits/{name}`" in line), None)
        assert row is not None, f"{name} is not in the README table"
        assert f"| {case['expected']} |" in row, (
            f"the README row for {name} states another decision"
        )


def test_the_workflow_example_runs_commitguard_and_is_hardened() -> None:
    text = (EXAMPLES / "github-actions" / "commitguard.yml").read_text(encoding="utf-8")
    inspection = inspect_workflow_text(Path(".github/workflows/commitguard.yml"), text)
    assert inspection is not None
    assert CHECK_NAME in inspection.check_names
    failures = [issue for issue in inspection.issues if issue.level is WorkflowIssueLevel.FAIL]
    assert failures == [], failures


def test_the_organization_example_floor_cannot_be_lowered() -> None:
    directory = EXAMPLES / "organization-policy"
    mandatory = parse_config((directory / "mandatory-policy.yaml").read_text(encoding="utf-8"))
    repository = parse_config((directory / "repository-attempt.yaml").read_text(encoding="utf-8"))
    effective = apply_mandatory_policies(build_policy_set(repository), mandatory)
    assert effective["ai_coauthor"].action is Action.BLOCK
    assert effective["ai_identity"].action is Action.BLOCK
    assert effective["ai_identity"].enabled


#: PyPI names that belong to OTHER projects. Never install these.
TAKEN_ON_PYPI = ("commitguard", "commitguard-cli")
#: This project's own PyPI distribution name (the import package is `commitguard`).
DISTRIBUTION = "commitguardian"


def test_no_documentation_installs_a_name_that_belongs_to_someone_else() -> None:
    """`pip install commitguard` installs an unrelated project: never suggest it.

    Both `commitguard` and `commitguard-cli` on PyPI belong to different authors,
    so a copied install line would send a user to someone else's code - a
    dependency-confusion risk of our own making. Installing this project means
    `commitguardian` from PyPI, or any name from a Git source.
    """
    root = EXAMPLES.parent
    offenders = []
    for path in sorted([*root.glob("*.md"), *root.glob("docs/**/*.md"), *EXAMPLES.rglob("*.md")]):
        in_code_block = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if not in_code_block:
                continue
            stripped = line.strip().removeprefix("$").strip()
            if not stripped.startswith(("pip install", "python -m pip install", "pipx install")):
                continue
            if "git+https://" in stripped or " -e " in stripped or stripped.endswith(" -e ."):
                continue  # installing from a Git source or the working tree is always fine
            # A plain index install: the name must be ours, as a whole word.
            words = stripped.replace('"', " ").replace("'", " ").replace("[", " [").split()
            named = {w for w in words if w.startswith("commitguard")}
            if named - {DISTRIBUTION}:
                offenders.append(f"{path.relative_to(root)}:{number}: {stripped}")
    assert offenders == [], (
        f"these install commands name a PyPI project that is not ours "
        f"({', '.join(TAKEN_ON_PYPI)} belong to other authors); use {DISTRIBUTION} "
        f"or a Git source:\n" + "\n".join(offenders)
    )


def test_the_distribution_name_is_not_one_that_belongs_to_someone_else() -> None:
    """The name in pyproject.toml is what `twine upload` would claim on PyPI."""
    pyproject = (EXAMPLES.parent / "pyproject.toml").read_text(encoding="utf-8")
    declared = next(
        line.split("=", 1)[1].strip().strip('"')
        for line in pyproject.splitlines()
        if line.startswith("name = ")
    )
    assert declared == DISTRIBUTION
    assert declared not in TAKEN_ON_PYPI
