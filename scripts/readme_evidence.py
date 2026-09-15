"""Collect the evidence rendered into the README images.

Runs the real test suites, static checks and CLI, and writes the results as
JSON to docs/evidence/. `cd web && npm run screenshots` renders them (together
with dashboard screenshots of the demo stack) into docs/images/.

    python scripts/readme_evidence.py              # tests, checks and CLI
    python scripts/readme_evidence.py --cli-only   # CLI transcripts only

Nothing is typed by hand: every number and every line of terminal output in
the images comes from a command run by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = ROOT / "docs" / "evidence"
VENV_BIN = ROOT / ".venv" / "bin"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def run(
    args: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None, input_: str = ""
) -> tuple[int, str, float]:
    started = time.monotonic()
    merged = {**os.environ, "NO_COLOR": "1", "COLUMNS": "100", **(env or {})}
    merged["PATH"] = f"{VENV_BIN}{os.pathsep}{merged.get('PATH', '')}"
    completed = subprocess.run(  # noqa: S603 - fixed argument lists, no shell
        args,
        cwd=cwd,
        env=merged,
        input=input_,
        capture_output=True,
        text=True,
        check=False,
    )
    output = ANSI.sub("", completed.stdout + completed.stderr)
    return completed.returncode, output, round(time.monotonic() - started, 1)


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def pytest_results(work: Path) -> dict[str, Any]:
    report = work / "pytest.xml"
    code, output, seconds = run(
        [str(VENV_BIN / "pytest"), "-q", "-p", "no:cacheprovider", f"--junitxml={report}"]
    )
    suite = ET.parse(report).getroot()  # noqa: S314 - file written by pytest above
    if suite.tag == "testsuites":
        suite = suite[0]
    total = int(suite.get("tests", 0))
    failed = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    return {
        "name": "pytest",
        "label": "Python unit + integration",
        "ok": code == 0,
        "passed": total - failed - skipped,
        "failed": failed,
        "skipped": skipped,
        "seconds": seconds,
        "summary": last_line(output),
    }


def vitest_results(work: Path) -> dict[str, Any]:
    report = work / "vitest.json"
    code, _, seconds = run(
        ["npx", "vitest", "run", "--reporter=json", f"--outputFile={report}"], cwd=WEB
    )
    data = json.loads(report.read_text())
    return {
        "name": "vitest",
        "label": "Dashboard components",
        "ok": code == 0,
        "passed": data["numPassedTests"],
        "failed": data["numFailedTests"],
        "skipped": data["numPendingTests"] + data["numTodoTests"],
        "seconds": seconds,
        "summary": f"{data['numPassedTests']} passed ({len(data['testResults'])} files)",
    }


def playwright_results(work: Path) -> dict[str, Any]:
    report = work / "playwright.json"
    code, _, seconds = run(
        ["npx", "playwright", "test", "--reporter=json"],
        cwd=WEB,
        env={"PLAYWRIGHT_JSON_OUTPUT_NAME": str(report)},
    )
    stats = json.loads(report.read_text())["stats"]
    return {
        "name": "playwright",
        "label": "Browser end-to-end + axe",
        "ok": code == 0,
        "passed": stats["expected"] + stats.get("flaky", 0),
        "failed": stats["unexpected"],
        "skipped": stats["skipped"],
        "seconds": seconds,
        "summary": f"{stats['expected']} passed against the full stack",
    }


def check(name: str, label: str, args: list[str], *, cwd: Path = ROOT) -> dict[str, Any]:
    code, output, seconds = run(args, cwd=cwd)
    return {
        "name": name,
        "label": label,
        "ok": code == 0,
        "seconds": seconds,
        "summary": last_line(output) if code == 0 or output.strip() else f"exit {code}",
    }


def cli_transcripts(work: Path) -> list[dict[str, Any]]:
    repo = work / "northwind-api"
    repo.mkdir()
    shown = "~/northwind-api"

    def git(*args: str, input_: str = "") -> None:
        code, output, _ = run(["git", *args], cwd=repo, input_=input_)
        if code != 0:
            raise RuntimeError(f"git {args[0]} failed: {output}")

    def session(commands: list[tuple[str, list[str]]]) -> dict[str, Any]:
        steps = []
        for display, args in commands:
            code, output, _ = run(args, cwd=repo)
            output = output.replace(str(repo), shown).replace(str(work), "~")
            steps.append({"command": display, "exit": code, "output": output.rstrip()})
        return {"cwd": shown, "steps": steps}

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Dana Reyes")
    git("config", "user.email", "dana@northwind.dev")
    (repo / "README.md").write_text("# northwind-api\n")
    git("add", "README.md")
    git("commit", "-q", "-m", "chore: initial commit")
    run(["commitguard", "init"], cwd=repo)
    run(["commitguard", "install"], cwd=repo)
    (repo / "auth.py").write_text("def refresh_session(): ...\n")
    git("add", "auth.py")

    subject, trailer = "feat: add session refresh", "Co-authored-by: Claude <noreply@anthropic.com>"
    commit = ["git", "commit", "-m", subject, "-m", trailer]
    hook = session([(f'git commit -m "{subject}" -m "{trailer}"', commit)])
    hook["id"] = "commit-blocked"
    hook["title"] = "Local hook blocks the commit"

    # Bypass the hook to create the commit a CI check would see, then scan it.
    git("commit", "-q", "--no-verify", "-m", subject, "-m", trailer)
    scan = session(
        [
            ("commitguard check HEAD~1..HEAD", ["commitguard", "check", "HEAD~1..HEAD"]),
            ("commitguard scan HEAD~1..HEAD", ["commitguard", "scan", "HEAD~1..HEAD"]),
        ]
    )
    scan["id"] = "scan-blocked"
    scan["title"] = "The same engine scans a range, as CI does"
    return [hook, scan]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cli-only", action="store_true", help="only refresh CLI transcripts")
    options = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="commitguard-evidence-"))
    try:
        _, version, _ = run([str(VENV_BIN / "commitguard"), "--version"])
        (OUT / "cli.json").write_text(
            json.dumps({"version": last_line(version), "sessions": cli_transcripts(work)}, indent=2)
            + "\n"
        )
        print("wrote docs/evidence/cli.json")
        if options.cli_only:
            return 0

        _, sha, _ = run(["git", "rev-parse", "--short", "HEAD"])
        _, dirty, _ = run(["git", "status", "--porcelain"])
        suites = [pytest_results(work), vitest_results(work), playwright_results(work)]
        checks = [
            check("ruff", "ruff lint", [str(VENV_BIN / "ruff"), "check", "."]),
            check("mypy", "mypy --strict", [str(VENV_BIN / "mypy")]),
            check("tsc", "TypeScript", ["npm", "run", "--silent", "typecheck"], cwd=WEB),
            check("eslint", "ESLint", ["npm", "run", "--silent", "lint"], cwd=WEB),
        ]
        for item in checks:
            if item["name"] in {"tsc", "eslint"} and item["ok"]:
                item["summary"] = "no errors"
        evidence = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "commit": sha.strip() + ("+local changes" if dirty.strip() else ""),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "suites": suites,
            "checks": checks,
        }
        (OUT / "tests.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print("wrote docs/evidence/tests.json")
        for item in suites + checks:
            print(f"  {'ok  ' if item['ok'] else 'FAIL'} {item['name']}: {item['summary']}")
        return 0 if all(item["ok"] for item in suites + checks) else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
