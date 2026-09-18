"""``.env.example`` must list exactly the environment variables the code reads.

It drifted badly once: after the GitHub App, the dashboard and notifications
landed, the file still said "CommitGuard does not read any environment
variables yet" and named a ``GITHUB_TOKEN`` nothing reads. Anyone deploying the
App from it would have configured nothing at all, so the sync is now enforced.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"COMMITGUARD_[A-Z0-9_]+")


def _documented() -> set[str]:
    return set(NAME.findall((ROOT / ".env.example").read_text(encoding="utf-8")))


def _read_by_the_code() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        names |= set(NAME.findall(path.read_text(encoding="utf-8")))
    return names


def test_the_example_file_was_found_and_is_not_empty() -> None:
    assert len(_documented()) > 20


def test_every_variable_the_code_reads_is_documented() -> None:
    missing = sorted(_read_by_the_code() - _documented())
    assert not missing, f".env.example does not mention: {', '.join(missing)}"


def test_no_variable_is_documented_that_nothing_reads() -> None:
    stale = sorted(_documented() - _read_by_the_code())
    assert not stale, f".env.example names variables no code reads: {', '.join(stale)}"


def test_no_secret_carries_a_value() -> None:
    """A committed example must never carry a real secret.

    ``*_FILE`` variables hold a path, not the secret, so an illustrative path is
    fine; the variables that hold the secret itself must be left empty.
    """
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip("# ").strip()
        if "=" not in stripped or not stripped.startswith("COMMITGUARD_"):
            continue
        name, _, value = stripped.partition("=")
        if name.endswith("_FILE"):
            continue
        secretish = ("SECRET", "PRIVATE_KEY", "SIGNING_KEY", "PASSWORD", "TOKEN")
        assert not (value and any(word in name for word in secretish)), line
