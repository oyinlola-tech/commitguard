"""Security properties of CI enforcement: untrusted metadata, forks, workflow commands."""

import re
from pathlib import Path

PWNED = Path("/tmp/commitguard-pwned")  # noqa: S108 - the marker the payloads try to create
PAYLOAD = "$(touch /tmp/commitguard-pwned)"
INJECTIONS = [
    PAYLOAD,
    "`touch /tmp/commitguard-pwned`",
    "; touch /tmp/commitguard-pwned",
    "&& touch /tmp/commitguard-pwned",
    "| touch /tmp/commitguard-pwned",
]
KNOWN_COMMANDS = re.compile(
    r"^::(error|warning|notice)( title=[^:]*)?::|^::stop-commands::[0-9a-f]{32}$"
)


def _command_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("::")]


def test_malicious_metadata_is_never_executed_or_interpreted(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    assert not PWNED.exists(), "remove /tmp/commitguard-pwned before running this test"
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    branch = "feat/$(touch${IFS}/tmp/commitguard-pwned);`id`|x"
    dev.git("checkout", "--quiet", "-B", branch, base)
    for payload in INJECTIONS:
        dev.commit(
            f"fix: {payload}\n\n::set-output name=result::allow\n"
            f"::add-mask::{payload}\n"
            f"Co-authored-by: Claude <noreply@anthropic.com> {payload}\n"
            f"Reviewed-by: {payload} <x@example.com>\n",
            author=f"Mallory {payload} <mallory@example.com>",
        )
    head = dev.commit(
        "x\n\nCo-authored-by: Claude <noreply@anthropic.com>\n::error::fake%0A::warning::fake\n"
    )
    dev.push(branch)
    ci = hub.ci_clone(checkout=head)
    payload = gh.pr_event(base, head)
    payload["pull_request"]["head"]["ref"] = branch
    result = run_ci(ci, "pull_request", payload)

    assert result.returncode == 1, result.output
    assert not PWNED.exists()
    # Untrusted text is only printed while workflow commands are disabled.
    lines = result.stdout.splitlines()
    token_lines = [i for i, line in enumerate(lines) if line.startswith("::stop-commands::")]
    assert len(token_lines) == 1
    token = lines[token_lines[0]].split("::")[2]
    resume = lines.index(f"::{token}::")
    for line in lines[: token_lines[0]] + lines[resume + 1 :]:
        if line.startswith("::"):
            assert KNOWN_COMMANDS.match(line), line
            assert "set-output" not in line.split("::", 2)[1]
    assert "::set-output" not in "\n".join(lines[: token_lines[0]] + lines[resume + 1 :])
    # Step outputs contain only fixed keys and enum/integer values.
    assert set(result.outputs) == {"result", "conclusion", "commits", "violations", "warnings"}
    assert result.outputs["result"] == "block"
    # The summary escapes Markdown/HTML in untrusted values.
    assert "<noreply@anthropic.com>" not in result.summary
    assert "&lt;noreply@anthropic\\.com&gt;" in result.summary


def test_fork_pull_request_needs_no_secrets_or_write_access(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    dev = hub.dev
    base = dev.git("rev-parse", "main")
    dev.git("checkout", "--quiet", "-B", "fork-feature", base)
    head = dev.commit(gh.AI)
    dev.push("fork-feature")
    ci = hub.ci_clone(checkout=head)
    # Make the clone read-only for objects/refs: the check must not write to it.
    before = ci.git("for-each-ref") + ci.git("status", "--porcelain")
    result = run_ci(
        ci,
        "pull_request",
        gh.pr_event(base, head, fork=True),
        "--format",
        "json",
        extra_env={"GITHUB_TOKEN": "", "ACTIONS_RUNTIME_TOKEN": ""},
    )
    assert result.returncode == 1
    data = result.json()
    assert data["ci"]["from_fork"] is True
    assert data["ci"]["policy_source"].startswith("pull request base")
    assert ci.git("for-each-ref") + ci.git("status", "--porcelain") == before


def test_json_mode_keeps_stdout_pure_json(hub, run_ci, gh) -> None:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    hub.dev.git("checkout", "--quiet", "-B", "f", base)
    head = hub.dev.commit(gh.AI)
    hub.dev.push("f")
    result = run_ci(
        hub.ci_clone(checkout=head), "pull_request", gh.pr_event(base, head), "-f", "json"
    )
    data = result.json()  # raises if annotations were mixed into stdout
    assert data["schema_version"] == 1
    assert "message" not in data["commits"][0]  # full messages are not included


def test_report_file(hub, run_ci, gh, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    base = hub.dev.git("rev-parse", "main")
    report = tmp_path / "commitguard.json"
    result = run_ci(
        hub.ci_clone(checkout=base),
        "pull_request",
        gh.pr_event(base, base),
        "--report-file",
        str(report),
    )
    assert result.returncode == 0
    assert '"action": "allow"' in report.read_text()
