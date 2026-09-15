"""Execute the composite Action's own shell steps (action.yml) outside GitHub.

`actions/setup-python` is replaced by the interpreter running the tests; every
`run:` script is taken verbatim from action.yml and executed with bash using the
same environment variables GitHub would provide. Installing the hash-pinned
dependencies needs PyPI, so this test is opt-in (COMMITGUARD_NETWORK_TESTS=1).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("COMMITGUARD_NETWORK_TESTS") != "1", reason="set COMMITGUARD_NETWORK_TESTS=1"
    ),
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash required"),
]


def _outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def test_action_installs_hashed_and_blocks_violation(hub, gh, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    install_step, scan_step = action["runs"]["steps"][1], action["runs"]["steps"][2]

    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "--quiet", "-B", "feature", base)
    head = hub.dev.commit(gh.AI)
    hub.dev.push("feature")
    workspace = hub.ci_clone(checkout=head, name="workspace")
    event = tmp_path / "event.json"
    event.write_text(__import__("json").dumps(gh.pr_event(base, head)))
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()

    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    env.update(
        {
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_ACTIONS": "true",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_EVENT_PATH": str(event),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
    )

    install_out = tmp_path / "install-output"
    install_out.write_text("")
    install = subprocess.run(
        ["bash", "-c", install_step["run"]],
        cwd=workspace.path,
        env={
            **env,
            "GITHUB_OUTPUT": str(install_out),
            "COMMITGUARD_BASE_PYTHON": sys.executable,
            "COMMITGUARD_SOURCE": str(ROOT),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    python = _outputs(install_out)["python"]
    assert python.startswith(str(runner_temp))

    scan_out = tmp_path / "scan-output"
    scan_out.write_text("")
    scan = subprocess.run(
        ["bash", "-c", scan_step["run"]],
        cwd=workspace.path,
        env={
            **env,
            "GITHUB_OUTPUT": str(scan_out),
            "COMMITGUARD_PYTHON": python,
            "INPUT_CONFIG": "",
            "INPUT_FAIL_ON": "block",
            "INPUT_MAX_COMMITS": "10000",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert scan.returncode == 1, scan.stdout + scan.stderr
    assert "Result: BLOCK" in scan.stdout
    assert _outputs(scan_out)["result"] == "block"
    # The installed copy uses its bundled rules, not a source tree.
    probe = subprocess.run(
        [python, "-c", "from commitguard.rules.loader import builtin_rules_dir as d; print(d())"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "site-packages" in probe.stdout
