import shutil
import subprocess
import sys

import pytest

from commitguard.exceptions.git import HookInstallError
from commitguard.git.hooks import (
    BEGIN_MARKER,
    END_MARKER,
    HookType,
    parse_managed_block,
    render_block,
    render_hook_file,
)


@pytest.mark.parametrize("hook", list(HookType))
def test_rendered_hooks_parse_as_intact(hook: HookType) -> None:
    text = render_hook_file(hook, "/opt/venv/bin/python")
    block = parse_managed_block(text)
    assert block is not None
    assert block.intact
    assert block.hook == hook.value
    assert block.python == "/opt/venv/bin/python"
    assert text.startswith("#!/bin/sh\n")


@pytest.mark.parametrize(
    "python", ["/path with spaces/python", "/it's/python", '/q"uote/$(id)/`x`/python', ""]
)
def test_interpreter_path_is_quoted_safely(python: str) -> None:
    block = parse_managed_block(render_hook_file(HookType.PRE_PUSH, python))
    assert block is not None
    assert block.python == python


def test_control_characters_in_interpreter_path_are_rejected() -> None:
    with pytest.raises(HookInstallError):
        render_block(HookType.PRE_COMMIT, "/bin/python\nrm -rf ~")


def test_edits_break_the_checksum() -> None:
    text = render_hook_file(HookType.PRE_PUSH, "/usr/bin/python3")
    tampered = text.replace("-P -m commitguard", "-m commitguard")
    block = parse_managed_block(tampered)
    assert block is not None
    assert not block.intact


@pytest.mark.parametrize(
    "text",
    [
        f"#!/bin/sh\n{BEGIN_MARKER}\necho\n",  # no END
        f"#!/bin/sh\n{END_MARKER}\n{BEGIN_MARKER}\n",  # reversed
        f"{BEGIN_MARKER}\n{END_MARKER}\n{BEGIN_MARKER}\n{END_MARKER}\n",  # two blocks
    ],
)
def test_corrupt_markers(text: str) -> None:
    with pytest.raises(HookInstallError):
        parse_managed_block(text)


def test_foreign_hooks_have_no_block() -> None:
    assert parse_managed_block("#!/bin/sh\necho hello\n") is None


def test_hooks_contain_no_detection_logic() -> None:
    for hook in HookType:
        text = render_hook_file(hook, "/usr/bin/python3").lower()
        for term in ("claude", "co-authored", "anthropic", "copilot", "grep"):
            assert term not in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
@pytest.mark.parametrize("hook", list(HookType))
def test_rendered_hooks_are_valid_sh(hook: HookType, tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / hook.value
    path.write_text(render_hook_file(hook, sys.executable.replace("\\", "/")), newline="\n")
    subprocess.run(["sh", "-n", str(path)], check=True)
