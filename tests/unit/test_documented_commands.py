"""Every command shown in the documentation must exist.

Documentation drifts quietly: a command is renamed, and the page that told people
to run it keeps telling them. Two pages did exactly that before this test
(`commitguard github doctor`, `commitguard benchmark security`), so the rule is
enforced rather than remembered.
"""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from commitguard.cli.app import app

ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = sorted(
    [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "examples").rglob("*.md"),
        *(ROOT / "award-evidence").glob("*.md"),
        *(ROOT / "evidence").rglob("*.md"),
    ]
)
#: Commands that take subcommands; anything else after the name is an argument.
GROUPS = {"policy", "hook", "ci", "github", "dashboard", "benchmark", "reproduce", "report"}
#: An invocation starts a line (optionally after a shell prompt), not mid-sentence.
INVOCATION = re.compile(r"^(?:\$\s*)?commitguard ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")
runner = CliRunner()


def _code(text: str) -> list[str]:
    """Inline code spans and fenced code block lines: prose is not an invocation."""
    snippets: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            snippets.append(line)
        else:
            snippets.extend(re.findall(r"`([^`]+)`", line))
    return snippets


def _documented() -> dict[tuple[str, ...], list[str]]:
    found: dict[tuple[str, ...], list[str]] = {}
    for path in DOCUMENTS:
        for snippet in _code(path.read_text(encoding="utf-8")):
            for group, sub in INVOCATION.findall(snippet.strip()):
                command = (group, sub) if group in GROUPS and sub else (group,)
                found.setdefault(command, []).append(str(path.relative_to(ROOT)))
    return found


DOCUMENTED = _documented()


def test_documentation_was_found() -> None:
    assert len(DOCUMENTS) > 50
    assert len(DOCUMENTED) > 20


@pytest.mark.parametrize("command", sorted(DOCUMENTED), ids=lambda c: " ".join(c))
def test_documented_command_exists(command: tuple[str, ...]) -> None:
    result = runner.invoke(app, [*command, "--help"])
    assert result.exit_code == 0, (
        f"`commitguard {' '.join(command)}` is documented in "
        f"{', '.join(sorted(set(DOCUMENTED[command]))[:3])} but does not exist"
    )
