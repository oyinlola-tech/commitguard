"""Regular-expression denial of service (ReDoS).

Every regular expression in ``src/commitguard`` - module-level compiled patterns
(found automatically), the API route parameter patterns and the few inline
patterns - is run against adversarial inputs of ``COMMITGUARD_REDOS_CHARS``
characters built to trigger backtracking: long runs of each pattern's own
characters with a mismatching end, repeated prefixes, separators that create
word boundaries, and near-miss credential shapes.

The budget is generous (linear matching of 50,000 characters takes a few
milliseconds) so slow CI machines do not fail spuriously, while quadratic
behaviour - seconds at this size - does. Two patterns failed when this test was
written and were rewritten: the JWT redaction pattern (4.6 s on 80 KB) and the
workflow ``secrets.`` check (2.7 s on 60 KB).
"""

import importlib
import os
import pkgutil
import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import commitguard
from commitguard.api.app import _CONVERTERS
from commitguard.github.workflow import inspect_workflow_text
from commitguard.security.secrets import SecretRedactor

CHARS = int(os.environ.get("COMMITGUARD_REDOS_CHARS", "50000"))
BUDGET_SECONDS = 0.5
SOURCE = Path(commitguard.__file__).parent

#: Patterns written inline (``re.fullmatch(r"...", value)``) rather than compiled at module level.
INLINE_PATTERNS = {
    "controlplane.pagination.decode_cursor": r"[A-Za-z0-9_-]+",
    "github.storage._split_statements": r";[ \t]*(?:\n|\Z)",
    "api.app._compile (template parameters)": r"\{([a-z_]+):([a-z]+)\}",
}


def _module_patterns() -> dict[str, re.Pattern[str] | re.Pattern[bytes]]:
    found: dict[str, re.Pattern[str] | re.Pattern[bytes]] = {}
    for info in pkgutil.walk_packages(commitguard.__path__, "commitguard."):
        module = importlib.import_module(info.name)
        for name, value in vars(module).items():
            values = value if isinstance(value, tuple | list) else (value,)
            for index, item in enumerate(values):
                if isinstance(item, re.Pattern):
                    label = f"{info.name}.{name}" + (f"[{index}]" if len(values) > 1 else "")
                    if all(item.pattern != known.pattern for known in found.values()):
                        found[label] = item
    return found


def _all_patterns() -> dict[str, re.Pattern[str] | re.Pattern[bytes]]:
    patterns = _module_patterns()
    for kind, (regex, _) in _CONVERTERS.items():
        patterns[f"api.app route parameter {kind}"] = re.compile(rf"\A(?P<value>{regex})\Z")
    for label, regex in INLINE_PATTERNS.items():
        patterns[label] = re.compile(regex)
    return patterns


PATTERNS = _all_patterns()


def adversarial_inputs(n: int) -> list[str]:
    singles = "aA0f-._ \t/@=:\n;{}$e\u00e9"
    inputs = [c * n for c in singles] + [c * n + "!" for c in singles]
    repeated = [
        "a-",
        "a.",
        ".a",
        "a.b",
        "a.a!",
        "eyJ",
        "eyJ-",
        "eyJaaaaa-",
        "eyJaaaaa.aaaaa-",
        "sha256=",
        "-----BEGIN PRIVATE KEY-----",
        "authorization:",
        "Authorization: a b ",
        "bearer ",
        "bearer -",
        "ghp_",
        "ghp_-",
        "github_pat_",
        "git version 1",
        "/pr-1",
        "a@",
        "a@a.",
        ";\t",
        "${{",
        "${{ secrets",
        "x/",
        "refs/heads/",
    ]
    inputs += [(unit * (n // len(unit) + 1))[:n] + "!" for unit in repeated]
    inputs += [
        "authorization: " + " " * n + "x",
        "a@" + "a." * (n // 2) + "!",
        "a" + ".a" * (n // 2),
    ]
    return inputs


def _exercise(pattern: re.Pattern[str] | re.Pattern[bytes], text: str) -> None:
    if isinstance(pattern.pattern, bytes):
        data = text.encode("utf-8")
        pattern.search(data)  # type: ignore[call-overload]
        pattern.match(data)  # type: ignore[call-overload]
        list(pattern.finditer(data))  # type: ignore[call-overload]
    else:
        pattern.search(text)  # type: ignore[call-overload]
        pattern.match(text)  # type: ignore[call-overload]
        list(pattern.finditer(text))  # type: ignore[call-overload]
        pattern.split(text)  # type: ignore[call-overload]


def test_the_inventory_covers_every_inline_regular_expression() -> None:
    inline = re.compile(
        r"\bre\.(?:fullmatch|match|search|sub|subn|split|findall|finditer)\(\s*r?b?[\"']"
    )
    offenders = []
    for path in sorted(SOURCE.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if inline.search(line):
                offenders.append(f"{path.relative_to(SOURCE)}:{number}")
    # Each is listed in INLINE_PATTERNS; a new inline pattern must be added there too.
    assert len(offenders) == len(INLINE_PATTERNS), offenders


def test_patterns_were_found() -> None:
    # A guard against the collector silently finding nothing.
    assert len(PATTERNS) >= 30


@pytest.mark.parametrize("label", sorted(PATTERNS))
def test_regular_expression_is_linear_on_adversarial_input(label: str) -> None:
    pattern = PATTERNS[label]
    worst = 0.0
    worst_input = ""
    for text in adversarial_inputs(CHARS):
        started = time.perf_counter()
        _exercise(pattern, text)
        elapsed = time.perf_counter() - started
        if elapsed > worst:
            worst, worst_input = elapsed, text[:24]
    _TIMINGS[label] = {"pattern": str(pattern.pattern)[:200], "worst_seconds": round(worst, 6)}
    assert worst < BUDGET_SECONDS, (
        f"{label} took {worst:.3f}s on {CHARS} characters starting {worst_input!r}"
    )


_TIMINGS: dict[str, dict[str, object]] = {}


@pytest.fixture(scope="module", autouse=True)
def _redos_evidence(evidence) -> Iterator[None]:  # type: ignore[no-untyped-def]
    yield
    if _TIMINGS:
        evidence.write(
            "redos.json",
            {
                "input_characters": CHARS,
                "budget_seconds": BUDGET_SECONDS,
                "adversarial_inputs_per_pattern": len(adversarial_inputs(CHARS)),
                "patterns": dict(sorted(_TIMINGS.items())),
            },
        )


def _timed(function: Callable[[], object]) -> float:
    started = time.perf_counter()
    function()
    return time.perf_counter() - started


@pytest.mark.parametrize("unit", ["eyJ-", "eyJaaaaa-", "eyJaaaaa.aaaaa-", "ghp_-", "bearer -"])
def test_redaction_of_untrusted_text_is_linear(unit: str) -> None:
    """Regression: redaction runs on untrusted text before it is truncated."""
    redactor = SecretRedactor()
    text = unit * (4 * CHARS // len(unit))
    assert _timed(lambda: redactor.redact(text)) < BUDGET_SECONDS


def test_redaction_still_removes_jwts() -> None:
    redactor = SecretRedactor()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl"
    for text, expected in [
        (f"token {jwt} end", "token [REDACTED] end"),
        (f"x-{jwt}", "x-[REDACTED]"),
        (f"({jwt}).", "([REDACTED])."),
        (f"{jwt}.extra", "[REDACTED].extra"),
        (f"{jwt} {jwt}", "[REDACTED] [REDACTED]"),
        ("eyJshort.abc.def", "eyJshort.abc.def"),
        ("version 1.2.3", "version 1.2.3"),
    ]:
        assert redactor.redact(text) == expected


def test_workflow_secret_check_is_linear_on_untrusted_workflows() -> None:
    """Regression: the App inspects workflow files fetched from monitored repositories."""
    text = "on: push\njobs:\n  a:\n    runs-on: x\n    env:\n      V: '" + "${{" * CHARS + "'\n"
    assert _timed(lambda: inspect_workflow_text(Path(".github/workflows/a.yml"), text)) < 5.0
