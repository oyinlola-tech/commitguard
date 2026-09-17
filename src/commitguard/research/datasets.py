# ruff: noqa: E501 - dataset literals are kept on one line so each case reads as a row.
"""The labelled commit dataset used to measure detection.

Scope. CommitGuard detects *attribution evidence* in commit metadata (trailers,
author and committer identities, tool footers). It does not and cannot
determine how code was written. The dataset therefore labels metadata, and a
"positive" case is a commit whose metadata the built-in policy must not allow
silently (decision WARN or BLOCK).

Labels. Every case states:

* ``expected_decision`` - the decision the built-in policy must reach;
* ``required_rules`` - rules that must be reported;
* ``permitted_rules`` - rules that may additionally be reported without being
  wrong (for example ``malformed_trailer`` next to ``ai_coauthor`` on a mangled
  trailer).

Labels come from the documented detection semantics (docs/detection-engine.md)
and, for adversarial cases, from the security requirement (the attribution is
still attribution). They are **not** derived from the current implementation,
so a mismatch is a measured failure, not a relabelling opportunity.

Classes::

    clean        ordinary human commits, including near-misses (Claude Shannon,
                 Claudette, human employees of AI vendors, the word "AI")
    violations   canonical attribution for every built-in agent
    variations   casing, whitespace, line endings, duplicates, Unicode, layout
    malformed    broken trailers and identities
    adversarial  evasion attempts: separators, invisible characters, homoglyphs,
                 escape sequences, trailer floods, very long messages
    generated    seeded combinations to reach volume (clean, AI and bot mixes)

The dataset is fully deterministic: the same version always produces the same
cases and the same fingerprint. Versions only ever add cases; an older version
can always be rebuilt (``build_dataset(version="1.0.0")``).

Version history::

    1.0.0  initial dataset (9,115 cases)
    1.1.0  cases written after 1.0.0 exposed a parser bypass, before running them:
           more leading-character and Unicode evasions, quoted and bulleted
           human trailers, squash-merge messages (false-positive probes)
    1.2.0  cases written after the Phase 10 property-based fuzzer found that a Unicode
           letter or number before a key (\u32acCo-authored-by:) hid attribution, before
           running them: alphanumeric-symbol prefixes, numbered human trailers and
           non-ASCII prose (false-positive probes)
    1.3.0  cases written after the same fuzzer found that default-ignorable characters
           which are not control or format characters (variation selectors, U+034F,
           Hangul fillers) hid a trailer key or an agent alias, before running them;
           emoji variation selectors and Hangul names (false-positive probes)
"""

import json
import random
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from commitguard.security.hashing import sha256_hex

DATASET_VERSION = "1.3.0"
DATASET_VERSIONS = ("1.0.0", "1.1.0", "1.2.0", "1.3.0")
DATASET_SEED = 20260917
GENERATED_CASES = 9_000

CaseClass = Literal["clean", "violations", "variations", "malformed", "adversarial", "generated"]
Decision = Literal["allow", "warn", "block"]
CLASSES: tuple[CaseClass, ...] = (
    "clean",
    "violations",
    "variations",
    "malformed",
    "adversarial",
    "generated",
)


class Person(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    email: str


class DatasetCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,80}$")
    case_class: CaseClass
    category: str
    description: str
    message: str
    author: Person
    committer: Person
    expected_decision: Decision
    required_rules: tuple[str, ...] = ()
    permitted_rules: tuple[str, ...] = ()

    @property
    def positive(self) -> bool:
        return self.expected_decision != "allow"


# --------------------------------------------------------------------------- #
# Identities
# --------------------------------------------------------------------------- #
HUMANS = (
    Person(name="Ada Lovelace", email="ada@example.com"),
    Person(name="Grace Hopper", email="grace.hopper@example.org"),
    Person(name="Alan Turing", email="alan@turing.example"),
    Person(name="Chinwe Okafor", email="chinwe.okafor@example.ng"),
    Person(name="Jos\u00e9 Mart\u00ednez", email="jose@example.es"),
    Person(name="\u674e\u96f7", email="lilei@example.cn"),
    Person(name="Sam O'Neil", email="sam.oneil@example.ie"),
    Person(name="Priya Raman", email="priya@example.in"),
)
DEV = HUMANS[0]

# Canonical attribution for each built-in agent: (agent, trailer identity).
AGENT_COAUTHORS: tuple[tuple[str, str], ...] = (
    ("claude", "Claude <noreply@anthropic.com>"),
    ("claude-prefix", "Claude Opus 4.5 (1M context) <noreply@anthropic.com>"),
    ("chatgpt", "ChatGPT <noreply@openai.com>"),
    (
        "openai-codex",
        "chatgpt-codex-connector[bot] <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>",
    ),
    ("copilot", "Copilot <198982749+Copilot@users.noreply.github.com>"),
    ("cursor", "Cursor Agent <cursoragent@cursor.com>"),
    ("gemini", "Gemini <gemini-code-assist@google.com>"),
    ("windsurf", "Windsurf <noreply@windsurf.com>"),
    ("codeium", "Codeium <noreply@codeium.com>"),
    ("cline", "Cline <noreply@cline.bot>"),
    ("roo-code", "Roo Code <noreply@roocode.com>"),
    ("devin", "Devin AI <158243242+devin-ai-integration[bot]@users.noreply.github.com>"),
    ("amazon-q", "Amazon Q Developer <208079219+amazon-q-developer[bot]@users.noreply.github.com>"),
    ("jules", "google-labs-jules[bot] <161369871+google-labs-jules[bot]@users.noreply.github.com>"),
    ("aider", "aider <aider@aider.chat>"),
    ("openhands", "openhands <openhands@all-hands.dev>"),
)

BOTS = (
    Person(name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com"),
    Person(
        name="github-actions[bot]", email="41898282+github-actions[bot]@users.noreply.github.com"
    ),
    Person(name="renovate[bot]", email="29139614+renovate[bot]@users.noreply.github.com"),
)

SUBJECTS = (
    "feat(auth): add session rotation on privilege change",
    "fix(api): reject negative page sizes",
    "docs: explain the retention settings",
    "refactor(storage): extract migration helpers",
    "test(hooks): cover detached HEAD pushes",
    "chore(deps): update pydantic to 2.8",
    "perf(scan): batch cat-file reads",
    "ci: run the security suite on pull requests",
)


def _case(
    case_id: str,
    case_class: CaseClass,
    category: str,
    description: str,
    message: str,
    decision: Decision,
    required: Sequence[str] = (),
    permitted: Sequence[str] = (),
    *,
    author: Person = DEV,
    committer: Person | None = None,
) -> DatasetCase:
    return DatasetCase(
        id=case_id,
        case_class=case_class,
        category=category,
        description=description,
        message=message,
        author=author,
        committer=committer or author,
        expected_decision=decision,
        required_rules=tuple(sorted(required)),
        permitted_rules=tuple(sorted(permitted)),
    )


def _msg(subject: str, *trailers: str, body: str = "") -> str:
    parts = [subject]
    if body:
        parts.append(body)
    if trailers:
        parts.append("\n".join(trailers))
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# Hand-written classes
# --------------------------------------------------------------------------- #
def _clean() -> Iterator[DatasetCase]:
    c: CaseClass = "clean"
    yield _case("clean-plain", c, "plain", "Subject only", "fix: typo in README\n", "allow")
    yield _case(
        "clean-body",
        c,
        "plain",
        "Subject and wrapped body",
        _msg(
            SUBJECTS[0],
            body="Rotate the session identifier whenever roles change so a\nstolen cookie cannot inherit new privileges.",
        ),
        "allow",
    )
    for index, human in enumerate(HUMANS[1:], start=1):
        yield _case(
            f"clean-human-coauthor-{index}",
            c,
            "human-coauthor",
            f"Human co-author {human.name}",
            _msg(SUBJECTS[index % len(SUBJECTS)], f"Co-authored-by: {human.name} <{human.email}>"),
            "allow",
        )
    yield _case(
        "clean-signoff",
        c,
        "human-trailers",
        "Signed-off-by, Reviewed-by and Fixes from humans",
        _msg(
            SUBJECTS[1],
            "Signed-off-by: Ada Lovelace <ada@example.com>",
            "Reviewed-by: Grace Hopper <grace.hopper@example.org>",
            "Fixes: #1284",
        ),
        "allow",
    )
    near_misses = (
        (
            "claude-shannon",
            "Claude Shannon <claude.shannon@example.edu>",
            "a human whose first name is Claude",
        ),
        ("claudette", "Claudette Colvin <claudette@example.org>", "a name containing 'Claude'"),
        (
            "claude-dupont",
            "Claude Dupont <claude.dupont@example.fr>",
            "a French human named Claude",
        ),
        ("anthropic-employee", "Jane Doe <jane@anthropic.com>", "a human at an AI vendor's domain"),
        (
            "openai-employee",
            "Wojciech Kowalski <wojciech@openai.com>",
            "a human at an AI vendor's domain",
        ),
        (
            "devin-human",
            "Devin Brown <devin.brown@example.com>",
            "ambiguous alias used by a human, no vendor domain",
        ),
        ("copilot-surname", "Maria Copilotti <maria@example.it>", "a surname containing 'Copilot'"),
        ("nimbus-agent", "Nimbus AI Agent <nimbus@example.com>", "an unlisted, AI-sounding name"),
    )
    for key, identity, description in near_misses:
        yield _case(
            f"clean-near-miss-{key}",
            c,
            "near-miss",
            f"Co-author is {description}",
            _msg(SUBJECTS[2], f"Co-authored-by: {identity}"),
            "allow",
        )
    wording = (
        "feat: use AI service for recommendations",
        "docs: compare Claude, ChatGPT and Copilot for code review",
        "fix: handle the 'Generated with' header in exported CSV",
        "chore: remove Co-authored-by parsing from the changelog script",
    )
    for index, subject in enumerate(wording):
        yield _case(
            f"clean-wording-{index}",
            c,
            "ai-wording",
            "Mentions AI only in prose",
            subject + "\n",
            "allow",
        )
    yield _case(
        "clean-generated-protoc",
        c,
        "tool-trailer",
        "Generated-by names a non-AI tool",
        _msg("build: regenerate protobuf stubs", "Generated-by: protoc 25.1"),
        "allow",
    )
    yield _case(
        "clean-ai-assisted-no",
        c,
        "tool-trailer",
        "AI-assisted: no is ignored",
        _msg("fix: close file handles", "AI-assisted: no"),
        "allow",
    )
    yield _case(
        "clean-unicode-body",
        c,
        "unicode",
        "Non-Latin text in subject and body",
        _msg(
            "docs: \u7ffb\u8bd1\u5b89\u88c5\u6307\u5357",
            body="\u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0440\u0430\u0437\u0434\u0435\u043b \u043e\u0431 \u0443\u0441\u0442\u0430\u043d\u043e\u0432\u043a\u0435. \U0001f680",
        ),
        "allow",
        author=HUMANS[5],
    )
    yield _case(
        "clean-long-body",
        c,
        "size",
        "A 64 KiB body of ordinary prose",
        _msg(
            "docs: import the design review notes",
            body=("The storage layer keeps immutable versions. " * 1500).strip(),
        ),
        "allow",
    )
    yield _case(
        "clean-merge-message",
        c,
        "merge",
        "Default merge commit message",
        "Merge branch 'feature/rotation' into main\n",
        "allow",
    )
    yield _case(
        "clean-revert",
        c,
        "revert",
        "Revert message quoting a commit id",
        _msg(
            'Revert "feat: add cache"',
            body="This reverts commit 3a91f02e7d5c2b1f0a9e8d7c6b5a493827160f5e.",
        ),
        "allow",
    )
    yield _case(
        "clean-url-trailer",
        c,
        "human-trailers",
        "Link trailer with a URL and a colon",
        _msg(SUBJECTS[3], "Link: https://example.com/issues/42#comment:3"),
        "allow",
    )


def _violations() -> Iterator[DatasetCase]:
    c: CaseClass = "violations"
    for agent, identity in AGENT_COAUTHORS:
        yield _case(
            f"violation-coauthor-{agent}",
            c,
            "ai-coauthor",
            f"Canonical co-author trailer for {agent}",
            _msg(SUBJECTS[0], f"Co-authored-by: {identity}"),
            "block",
            ["ai_coauthor"],
        )
    yield _case(
        "violation-author-claude",
        c,
        "ai-identity",
        "The commit author is an AI agent",
        _msg(SUBJECTS[1]),
        "block",
        ["ai_identity"],
        author=Person(name="Claude", email="noreply@anthropic.com"),
    )
    yield _case(
        "violation-committer-copilot",
        c,
        "ai-identity",
        "The committer is an AI agent, the author a human",
        _msg(SUBJECTS[1]),
        "block",
        ["ai_identity"],
        committer=Person(name="Copilot", email="198982749+Copilot@users.noreply.github.com"),
    )
    footers = (
        (
            "claude-code-footer",
            "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)",
        ),
        ("claude-code-footer-plain", "Generated with Claude Code"),
    )
    for key, line in footers:
        yield _case(
            f"violation-{key}",
            c,
            "tool-footer",
            f"Tool footer: {line}",
            _msg(SUBJECTS[2], body=line),
            "block",
            ["ai_trailer"],
        )
    yield _case(
        "violation-ai-generated-key",
        c,
        "ai-trailer",
        "AI-Generated-By trailer (the key declares AI generation)",
        _msg(SUBJECTS[3], "AI-Generated-By: internal tooling"),
        "block",
        ["ai_trailer"],
    )
    yield _case(
        "violation-generated-by-agent",
        c,
        "ai-trailer",
        "Generated-by names an AI agent",
        _msg(SUBJECTS[3], "Generated-by: Claude Code"),
        "block",
        ["ai_trailer"],
    )
    yield _case(
        "violation-reviewed-by-agent",
        c,
        "ai-trailer",
        "Reviewed-by names an AI agent",
        _msg(SUBJECTS[4], "Reviewed-by: Copilot <198982749+Copilot@users.noreply.github.com>"),
        "block",
        ["ai_trailer"],
    )
    for bot in BOTS:
        yield _case(
            f"violation-bot-{bot.name.split('[')[0]}",
            c,
            "bot-identity",
            f"Automation account author {bot.name} (warn by default)",
            _msg("chore(deps): bump actions/checkout"),
            "warn",
            ["bot_identity"],
            author=bot,
        )
    yield _case(
        "violation-multiple-agents",
        c,
        "multiple",
        "Two AI co-authors and a human",
        _msg(
            SUBJECTS[0],
            "Co-authored-by: Claude <noreply@anthropic.com>",
            "Co-authored-by: Grace Hopper <grace.hopper@example.org>",
            "Co-authored-by: Cursor Agent <cursoragent@cursor.com>",
        ),
        "block",
        ["ai_coauthor"],
    )


def _variations() -> Iterator[DatasetCase]:
    c: CaseClass = "variations"
    claude = "Claude <noreply@anthropic.com>"
    keys = {
        "lower": "co-authored-by",
        "upper": "CO-AUTHORED-BY",
        "title": "Co-Authored-By",
        "mixed": "cO-aUtHoReD-bY",
    }
    for key_id, key in keys.items():
        yield _case(
            f"variation-case-{key_id}",
            c,
            "casing",
            f"Key casing {key}",
            _msg(SUBJECTS[0], f"{key}: {claude}"),
            "block",
            ["ai_coauthor"],
        )
    yield _case(
        "variation-email-case",
        c,
        "casing",
        "Upper-case email",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <NOREPLY@ANTHROPIC.COM>"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-name-case",
        c,
        "casing",
        "Lower-case name with vendor email",
        _msg(SUBJECTS[0], "Co-authored-by: claude <noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-extra-spaces",
        c,
        "whitespace",
        "Several spaces inside the value",
        _msg(SUBJECTS[0], "Co-authored-by:    Claude    <noreply@anthropic.com>   "),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "variation-crlf",
        c,
        "line-endings",
        "CRLF line endings",
        _msg(SUBJECTS[0], f"Co-authored-by: {claude}").replace("\n", "\r\n"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-cr-only",
        c,
        "line-endings",
        "Old Mac CR line endings",
        _msg(SUBJECTS[0], f"Co-authored-by: {claude}").replace("\n", "\r"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "variation-no-final-newline",
        c,
        "line-endings",
        "No trailing newline",
        f"{SUBJECTS[0]}\n\nCo-authored-by: {claude}",
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-duplicate",
        c,
        "duplicates",
        "The same trailer three times",
        _msg(SUBJECTS[0], *(f"Co-authored-by: {claude}",) * 3),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-mixed-trailers",
        c,
        "layout",
        "AI trailer among ten human trailers",
        _msg(
            SUBJECTS[0],
            *(f"Signed-off-by: {h.name} <{h.email}>" for h in HUMANS),
            f"Co-authored-by: {claude}",
            "Refs: #7",
        ),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-outside-block",
        c,
        "layout",
        "Trailer in the body, not in the final paragraph",
        _msg(
            SUBJECTS[0], "Refs: #9", body=f"Co-authored-by: {claude}\n\nMore explanation follows."
        ),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-subject-trailer",
        c,
        "layout",
        "Attribution as the subject line",
        f"Co-authored-by: {claude}\n",
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "variation-indented",
        c,
        "layout",
        "Indented trailer line",
        _msg(
            SUBJECTS[0],
            "Signed-off-by: Ada Lovelace <ada@example.com>",
            f"    Co-authored-by: {claude}",
        ),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "variation-multiline-message",
        c,
        "layout",
        "Long multi-paragraph message",
        _msg(
            SUBJECTS[0],
            f"Co-authored-by: {claude}",
            body="\n\n".join(f"Paragraph {i}: details." for i in range(40)),
        ),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-unicode-name",
        c,
        "unicode",
        "Full-width letters in the name",
        _msg(
            SUBJECTS[0],
            "Co-authored-by: \uff23\uff4c\uff41\uff55\uff44\uff45 <noreply@anthropic.com>",
        ),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-github-login",
        c,
        "email-format",
        "GitHub noreply login form",
        _msg(SUBJECTS[0], "Co-authored-by: Copilot <198982749+Copilot@users.noreply.github.com>"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-automation-local-part",
        c,
        "email-format",
        "Automation local part at a vendor domain",
        _msg(SUBJECTS[0], "Co-authored-by: Assistant <bot@anthropic.com>"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "variation-coauthor-alias-key",
        c,
        "key-alias",
        "Co-author key alias",
        _msg(SUBJECTS[0], f"Co-author: {claude}"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "variation-bot-coauthor",
        c,
        "bot-identity",
        "Automation account as co-author",
        _msg(
            SUBJECTS[5],
            "Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
        ),
        "warn",
        ["bot_identity"],
    )


def _malformed() -> Iterator[DatasetCase]:
    c: CaseClass = "malformed"
    yield _case(
        "malformed-empty-value",
        c,
        "empty",
        "Co-authored-by with no value",
        _msg(SUBJECTS[0], "Co-authored-by:"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-human-no-email",
        c,
        "identity",
        "Human co-author without an email",
        _msg(SUBJECTS[0], "Co-authored-by: Ada Lovelace"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-invalid-email",
        c,
        "identity",
        "Co-author with an invalid address",
        _msg(SUBJECTS[0], "Co-authored-by: <invalid>"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-empty-brackets",
        c,
        "identity",
        "Human co-author with empty brackets",
        _msg(SUBJECTS[0], "Co-authored-by: Grace Hopper <>"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-unbalanced",
        c,
        "identity",
        "Unbalanced angle brackets",
        _msg(SUBJECTS[0], "Co-authored-by: Grace Hopper <grace.hopper@example.org"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-no-separator-human",
        c,
        "separator",
        "Human co-author without a colon",
        _msg(SUBJECTS[0], "Co-authored-by Grace Hopper <grace.hopper@example.org>"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-signoff-no-colon",
        c,
        "separator",
        "Signed-off-by without a colon",
        _msg(SUBJECTS[0], "Signed-off-by Ada Lovelace <ada@example.com>"),
        "warn",
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-claude-no-email",
        c,
        "identity",
        "Co-author named exactly Claude, no email (medium confidence)",
        _msg(SUBJECTS[0], "Co-authored-by: Claude"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "malformed-claude-no-brackets",
        c,
        "separator",
        "No colon and no brackets around an AI email",
        _msg(SUBJECTS[0], "CO-AUTHORED-BY Claude noreply@anthropic.com"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )


def _adversarial() -> Iterator[DatasetCase]:
    c: CaseClass = "adversarial"
    claude = "Claude <noreply@anthropic.com>"
    separators = {
        "space-before-colon": "Co-authored-by : ",
        "tab-before-colon": "Co-authored-by\t: ",
        "no-space-after-colon": "Co-authored-by:",
        "spaces-in-key": "Co authored by: ",
        "underscores-in-key": "Co_authored_by: ",
        "double-colon": "Co-authored-by:: ",
    }
    for key_id, prefix in separators.items():
        yield _case(
            f"adversarial-separator-{key_id}",
            c,
            "separator",
            f"Evasive separator {prefix!r}",
            _msg(SUBJECTS[0], prefix + claude),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    invisible = {
        "zero-width-space-key": "Co-authored\u200b-by: " + claude,
        "zero-width-joiner-name": "Co-authored-by: Cl\u200daude <noreply@anthropic.com>",
        "bidi-override-name": "Co-authored-by: \u202eClaude <noreply@anthropic.com>",
        "soft-hyphen-key": "Co-authored\u00ad-by: " + claude,
        "nbsp-separator": "Co-authored-by:\u00a0" + claude,
    }
    for key_id, line in invisible.items():
        yield _case(
            f"adversarial-invisible-{key_id}",
            c,
            "invisible",
            f"Invisible characters: {key_id}",
            _msg(SUBJECTS[0], line),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    homoglyphs = {
        "cyrillic-a": "Co-authored-by: Cl\u0430ude <noreply@anthropic.com>",
        "greek-omicron-email": "Co-authored-by: Claude <n\u03bfreply@anthropic.com>",
        "cyrillic-o-key": "C\u043e-authored-by: " + claude,
    }
    for key_id, line in homoglyphs.items():
        yield _case(
            f"adversarial-homoglyph-{key_id}",
            c,
            "homoglyph",
            f"Look-alike letters: {key_id}",
            _msg(SUBJECTS[0], line),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    yield _case(
        "adversarial-unicode-line-separator",
        c,
        "line-separator",
        "U+2028 line separator before the trailer",
        SUBJECTS[0] + "\u2028\u2028Co-authored-by: " + claude,
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-escape-sequences",
        c,
        "terminal",
        "ANSI escape codes after the address",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <noreply@anthropic.com>\x1b[1A\x1b[2K"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-folded-continuation",
        c,
        "layout",
        "Trailer hidden as an indented continuation",
        _msg(SUBJECTS[0], "Refs: #7", "  Co-authored-by: " + claude),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-trailer-flood",
        c,
        "flood",
        "1,200 human trailers before the attribution (beyond the parser bound)",
        _msg(
            SUBJECTS[0],
            *(f"Signed-off-by: Person {i} <p{i}@example.com>" for i in range(1200)),
            "Co-authored-by: " + claude,
        ),
        "block",
        [],
        ["ai_coauthor", "ai_identity", "ai_trailer", "bot_identity", "malformed_trailer"],
    )
    yield _case(
        "adversarial-very-long-message",
        c,
        "size",
        "1 MiB of text before the attribution",
        _msg(SUBJECTS[0], "Co-authored-by: " + claude, body="x" * (1024 * 1024)),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "adversarial-huge-name",
        c,
        "size",
        "A 100 KiB co-author name",
        _msg(SUBJECTS[0], "Co-authored-by: " + "A" * (100 * 1024) + " <noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-replacement-characters",
        c,
        "encoding",
        "U+FFFD from malformed UTF-8 around the trailer",
        _msg(SUBJECTS[0], "\ufffdCo-authored-by: " + claude + "\ufffd"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-html-entities",
        c,
        "encoding",
        "HTML-escaped brackets",
        _msg(SUBJECTS[0], "Co-authored-by: Claude &lt;noreply@anthropic.com&gt;"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-mailto",
        c,
        "email-format",
        "mailto: inside the brackets",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <mailto:noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-plus-address",
        c,
        "email-format",
        "Plus-addressed vendor email",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <noreply+x@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-subdomain",
        c,
        "email-format",
        "Vendor subdomain email",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <noreply@mail.anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-name-only-prefix",
        c,
        "identity",
        "Distinctive name prefix with a non-vendor email",
        _msg(SUBJECTS[0], "Co-authored-by: Claude Sonnet 4.5 <x@example.com>"),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "adversarial-shell-metacharacters",
        c,
        "injection",
        "Shell and workflow-command metacharacters in the value",
        _msg(
            "feat: $(touch /tmp/pwned) `id`",
            "Co-authored-by: Claude <noreply@anthropic.com>; rm -rf / #",
        ),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-workflow-command",
        c,
        "injection",
        "A GitHub Actions workflow command in the subject",
        _msg("::add-mask::secret", "Co-authored-by: " + claude),
        "block",
        ["ai_coauthor"],
    )
    yield _case(
        "adversarial-null-character",
        c,
        "encoding",
        "A NUL character inside the message",
        _msg(SUBJECTS[0], "Co-authored-by: Claude\x00 <noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )


def _adversarial_1_1() -> Iterator[DatasetCase]:
    c: CaseClass = "adversarial"
    claude = "Claude <noreply@anthropic.com>"
    prefixes = {
        "question-mark": "?",
        "quote": "> ",
        "bullet": "\u2022 ",
        "asterisk": "* ",
        "hash": "#",
        "guillemet": "\u00bb",
        "double-replacement": "\ufffd\ufffd",
    }
    for key_id, prefix in prefixes.items():
        yield _case(
            f"adversarial-prefix-{key_id}",
            c,
            "leading-characters",
            f"Symbols before the key: {prefix!r}",
            _msg(SUBJECTS[0], prefix + "Co-authored-by: " + claude),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    yield _case(
        "adversarial-fullwidth-colon",
        c,
        "separator",
        "Full-width colon separator",
        _msg(SUBJECTS[0], "Co-authored-by\uff1a" + claude),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-fullwidth-brackets",
        c,
        "email-format",
        "Full-width angle brackets",
        _msg(SUBJECTS[0], "Co-authored-by: Claude \uff1cnoreply@anthropic.com\uff1e"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-quoted-name",
        c,
        "identity",
        "Quoted display name",
        _msg(SUBJECTS[0], 'Co-authored-by: "Claude" <noreply@anthropic.com>'),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-name-comment",
        c,
        "identity",
        "Parenthesised comment in the name",
        _msg(SUBJECTS[0], "Co-authored-by: Claude (Anthropic) <noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-trailing-dot-domain",
        c,
        "email-format",
        "Fully qualified domain with a trailing dot",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <noreply@anthropic.com.>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-two-addresses",
        c,
        "identity",
        "A second address after the AI address",
        _msg(SUBJECTS[0], "Co-authored-by: Claude <noreply@anthropic.com> <ada@example.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-split-continuation",
        c,
        "layout",
        "Address folded onto a continuation line",
        _msg(SUBJECTS[0], "Co-authored-by: Claude", " <noreply@anthropic.com>"),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-homoglyph-key-many",
        c,
        "homoglyph",
        "Several Cyrillic look-alikes in the key",
        _msg(SUBJECTS[0], "\u0421\u043e-\u0430uth\u043er\u0435d-b\u0443: " + claude),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-author-invisible-email",
        c,
        "invisible",
        "Zero-width space inside the author email",
        _msg(SUBJECTS[1]),
        "block",
        ["ai_identity"],
        author=Person(name="Claude", email="no\u200breply@anthropic.com"),
    )
    yield _case(
        "adversarial-committer-alias",
        c,
        "identity",
        "Committer name is an agent alias with a personal email",
        _msg(SUBJECTS[1]),
        "block",
        ["ai_identity"],
        committer=Person(name="Claude Code", email="ada@example.com"),
    )
    yield _case(
        "adversarial-signoff-agent",
        c,
        "ai-trailer",
        "Signed-off-by names an AI agent",
        _msg(SUBJECTS[2], "Signed-off-by: " + claude),
        "block",
        ["ai_trailer"],
    )
    yield _case(
        "adversarial-footer-emoji",
        c,
        "tool-footer",
        "Tool footer with a different leading emoji",
        _msg(SUBJECTS[2], body="\u2728 Generated with Claude Code"),
        "block",
        ["ai_trailer"],
    )


def _clean_1_1() -> Iterator[DatasetCase]:
    c: CaseClass = "clean"
    yield _case(
        "clean-quoted-signoff",
        c,
        "quoted",
        "A human sign-off quoted in the body",
        _msg(
            SUBJECTS[3],
            body="From the mailing list:\n\n> Signed-off-by: Ada Lovelace <ada@example.com>\n\nApplied with minor changes.",
        ),
        "allow",
    )
    yield _case(
        "clean-bullet-reviewer",
        c,
        "bulleted",
        "Bulleted human reviewer",
        _msg(
            SUBJECTS[3], body="Reviewers:\n- Reviewed-by: Grace Hopper <grace.hopper@example.org>"
        ),
        "allow",
    )
    yield _case(
        "clean-bullet-human-coauthor",
        c,
        "bulleted",
        "Bulleted human co-author",
        _msg(SUBJECTS[3], body="Pairing:\n* Co-authored-by: Alan Turing <alan@turing.example>"),
        "allow",
    )
    yield _case(
        "clean-dash-human-coauthor",
        c,
        "bulleted",
        "Dash-listed human co-author",
        _msg(SUBJECTS[3], body="Pairing:\n- Co-authored-by: Alan Turing <alan@turing.example>"),
        "allow",
    )
    yield _case(
        "clean-squash-merge",
        c,
        "merge",
        "GitHub squash merge with a human co-author",
        "feat: add rotation (#12)\n\n* feat: rotate on role change\n\n* fix: keep old sessions valid until expiry\n\n---------\n\nCo-authored-by: Grace Hopper <grace.hopper@example.org>\n",
        "allow",
    )
    yield _case(
        "clean-claude-monet",
        c,
        "near-miss",
        "A human named Claude Monet",
        _msg(SUBJECTS[4], "Co-authored-by: Claude Monet <claude@monet.example>"),
        "allow",
    )
    yield _case(
        "clean-web-flow-committer",
        c,
        "near-miss",
        "GitHub web-flow committer",
        _msg(SUBJECTS[4]),
        "allow",
        committer=Person(name="GitHub", email="noreply@github.com"),
    )
    yield _case(
        "clean-markdown-quote",
        c,
        "quoted",
        "A quoted discussion without trailers",
        _msg(SUBJECTS[5], body="> Should we use an AI reviewer?\n\nNot for this change."),
        "allow",
    )


# --------------------------------------------------------------------------- #
# Generated volume
# --------------------------------------------------------------------------- #
KEY_FORMS = ("Co-authored-by", "co-authored-by", "Co-Authored-By", "CO-AUTHORED-BY")


def _generated(count: int, seed: int) -> Iterator[DatasetCase]:
    rng = random.Random(seed)  # noqa: S311 - deterministic dataset, not security randomness
    c: CaseClass = "generated"
    for index in range(count):
        kind = rng.choices(("clean", "ai", "bot"), weights=(60, 35, 5))[0]
        subject = rng.choice(SUBJECTS)
        humans = rng.sample(HUMANS, k=rng.randint(0, 3))
        human_trailers = [
            f"{rng.choice(('Co-authored-by', 'Signed-off-by', 'Reviewed-by'))}: {h.name} <{h.email}>"
            for h in humans
        ]
        body = " ".join(
            rng.choice(("Update tests.", "Handle errors.", "Refactor.", "Add docs."))
            for _ in range(rng.randint(0, 20))
        )
        author = rng.choice(HUMANS)
        if kind == "clean":
            yield _case(
                f"generated-{index:05d}",
                c,
                "clean",
                "Generated human commit",
                _msg(subject, *human_trailers, body=body),
                "allow",
                author=author,
            )
        elif kind == "ai":
            agent, identity = rng.choice(AGENT_COAUTHORS)
            trailers = [*human_trailers, f"{rng.choice(KEY_FORMS)}: {identity}"]
            rng.shuffle(trailers)
            yield _case(
                f"generated-{index:05d}",
                c,
                "ai-coauthor",
                f"Generated AI co-author ({agent})",
                _msg(subject, *trailers, body=body),
                "block",
                ["ai_coauthor"],
                author=author,
            )
        else:
            bot = rng.choice(BOTS)
            yield _case(
                f"generated-{index:05d}",
                c,
                "bot-identity",
                "Generated automation account author",
                _msg("chore(deps): bump a dependency", body=body),
                "warn",
                ["bot_identity"],
                author=bot,
            )


def _adversarial_1_2() -> Iterator[DatasetCase]:
    c: CaseClass = "adversarial"
    claude = "Claude <noreply@anthropic.com>"
    # Characters that are letters or numbers to Unicode (str.isalnum) or become ASCII under
    # NFKC, written directly before the key. Found by the Phase 10 property-based fuzzer.
    prefixes = {
        "circled-ideograph": "\u32ac",
        "circled-latin-letter": "\u24de",
        "circled-digit": "\u2460",
        "roman-numeral": "\u216b",
        "superscript-two": "\u00b2",
        "arabic-indic-digit": "\u0663",
        "cjk-ideograph": "\u91d1",
        "fullwidth-letter": "\uff58",
        "mixed-fuzzer-prefix": "\u24de\u0830\u02e5\u00b6\u0fc1\u0839\u07f8\u0375\u02d4",
    }
    for key_id, prefix in prefixes.items():
        yield _case(
            f"adversarial-alnum-prefix-{key_id}",
            c,
            "leading-characters",
            f"Unicode letter or number before the key: {prefix!r}",
            _msg(SUBJECTS[0], prefix + "Co-authored-by: " + claude),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    yield _case(
        "adversarial-alnum-prefix-random-case",
        c,
        "leading-characters",
        "Unicode number before a randomly cased key without a space after the colon",
        _msg(SUBJECTS[0], "\u32acCo-AUThorEd-By:" + claude),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )
    yield _case(
        "adversarial-alnum-prefix-among-human-trailers",
        c,
        "leading-characters",
        "Prefixed AI trailer between human trailers",
        _msg(
            SUBJECTS[0],
            "Signed-off-by: Ada Lovelace <ada@example.com>",
            "\u2460Co-authored-by: " + claude,
            "Reviewed-by: Grace Hopper <grace.hopper@example.org>",
        ),
        "block",
        ["ai_coauthor"],
        ["malformed_trailer"],
    )


def _clean_1_2() -> Iterator[DatasetCase]:
    c: CaseClass = "clean"
    yield _case(
        "clean-numbered-human-coauthor",
        c,
        "bulleted",
        "Circled-number list of human co-authors",
        _msg(
            SUBJECTS[3],
            body="Pairing:\n\u2460 Co-authored-by: Alan Turing <alan@turing.example>\n\u2461 Co-authored-by: Grace Hopper <grace.hopper@example.org>",
        ),
        "allow",
    )
    yield _case(
        "clean-roman-numeral-reviewer",
        c,
        "bulleted",
        "Roman-numeral list item naming a human reviewer",
        _msg(SUBJECTS[3], body="\u216b. Reviewed-by: Grace Hopper <grace.hopper@example.org>"),
        "allow",
    )
    yield _case(
        "clean-non-ascii-prose-with-colon",
        c,
        "prose",
        "Non-ASCII prose lines that contain colons",
        _msg(
            SUBJECTS[3],
            body="Gr\u00f6\u00dfe: 5 MB\n\u65e5\u672c\u8a9e: \u30c6\u30b9\u30c8\nCaf\u00e9: open until 5",
        ),
        "allow",
    )
    yield _case(
        "clean-non-ascii-human-trailers",
        c,
        "identity",
        "Human trailers with non-ASCII names",
        _msg(
            SUBJECTS[3],
            "Co-authored-by: Jos\u00e9 N\u00fa\u00f1ez <jose@example.com>",
            "Signed-off-by: \u5c71\u7530\u592a\u90ce <yamada@example.jp>",
        ),
        "allow",
    )


def _adversarial_1_3() -> Iterator[DatasetCase]:
    c: CaseClass = "adversarial"
    # Default-ignorable code points that are not control or format characters: they render
    # as nothing but survived normalisation. Found by the Phase 10 property-based fuzzer.
    ignorable = {
        "combining-grapheme-joiner": "͏",
        "variation-selector-16": "️",
        "variation-selector-17": "\U000e0100",
        "mongolian-variation-selector": "᠋",
        "hangul-filler": "ㅤ",
        "halfwidth-hangul-filler": "ﾠ",
        "khmer-inherent-vowel": "឴",
    }
    for key_id, char in ignorable.items():
        yield _case(
            f"adversarial-ignorable-key-{key_id}",
            c,
            "invisible-characters",
            f"Default-ignorable {char!r} inside the trailer key",
            _msg(SUBJECTS[0], f"Co-authored{char}-by: Claude <noreply@anthropic.com>"),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
        yield _case(
            f"adversarial-ignorable-name-{key_id}",
            c,
            "invisible-characters",
            f"Default-ignorable {char!r} inside an agent alias with a personal email",
            _msg(SUBJECTS[0], f"Co-authored-by: Clau{char}de Code <dev@example.com>"),
            "block",
            ["ai_coauthor"],
            ["malformed_trailer"],
        )
    yield _case(
        "adversarial-ignorable-author-alias",
        c,
        "invisible-characters",
        "Author name is an agent alias containing a variation selector",
        _msg(SUBJECTS[1]),
        "block",
        ["ai_identity"],
        author=Person(name="Clau️de Code", email="dev@example.com"),
    )


def _clean_1_3() -> Iterator[DatasetCase]:
    c: CaseClass = "clean"
    yield _case(
        "clean-emoji-variation-selector",
        c,
        "unicode",
        "Human commit whose subject and co-author name contain emoji variation selectors",
        _msg(
            "docs: add ❤️ to the changelog",
            "Co-authored-by: Grace Hopper ⭐️ <grace.hopper@example.org>",
        ),
        "allow",
    )
    yield _case(
        "clean-hangul-name",
        c,
        "identity",
        "Human Korean co-author",
        _msg(SUBJECTS[3], "Co-authored-by: 김민준 <minjun@example.kr>"),
        "allow",
    )


# --------------------------------------------------------------------------- #
# Dataset
# --------------------------------------------------------------------------- #
def build_dataset(
    *, version: str = DATASET_VERSION, generated: int = GENERATED_CASES, seed: int = DATASET_SEED
) -> list[DatasetCase]:
    if version not in DATASET_VERSIONS:
        raise ValueError(f"unknown dataset version {version}; known: {', '.join(DATASET_VERSIONS)}")
    cases = [*_clean(), *_violations(), *_variations(), *_malformed(), *_adversarial()]
    if version >= "1.1.0":
        cases += [*_clean_1_1(), *_adversarial_1_1()]
    if version >= "1.2.0":
        cases += [*_clean_1_2(), *_adversarial_1_2()]
    if version >= "1.3.0":
        cases += [*_clean_1_3(), *_adversarial_1_3()]
    cases += list(_generated(generated, seed))
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1}) if len(set(ids)) != len(ids) else []
    if duplicates:
        raise ValueError(f"duplicate case ids: {', '.join(duplicates)}")
    return cases


def fingerprint(cases: Iterable[DatasetCase]) -> str:
    lines = (
        json.dumps(case.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
        for case in cases
    )
    return sha256_hex("\n".join(lines).encode("utf-8"))


def write_dataset(
    cases: Sequence[DatasetCase], directory: Path, *, version: str = DATASET_VERSION
) -> dict[str, object]:
    """Write one JSONL file per class plus a manifest. Returns the manifest."""
    directory.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for case_class in CLASSES:
        selected = [case for case in cases if case.case_class == case_class]
        counts[case_class] = len(selected)
        target = directory / case_class / "cases.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            for case in selected:
                handle.write(
                    json.dumps(case.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
                    + "\n"
                )
    manifest: dict[str, object] = {
        "dataset": "commitguard-attribution",
        "version": version,
        "seed": DATASET_SEED,
        "cases": len(cases),
        "classes": counts,
        "positive": sum(1 for case in cases if case.positive),
        "negative": sum(1 for case in cases if not case.positive),
        "fingerprint": fingerprint(cases),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def load_dataset(directory: Path) -> list[DatasetCase]:
    """Load a dataset written by :func:`write_dataset` (or any compatible JSONL set)."""
    cases: list[DatasetCase] = []
    files = sorted(directory.glob("*/cases.jsonl")) or sorted(directory.glob("*.jsonl"))
    if not files:
        raise ValueError(f"no dataset files (*/cases.jsonl) in {directory}")
    for path in files:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if line.strip():
                    try:
                        cases.append(DatasetCase.model_validate_json(line))
                    except ValueError as exc:
                        raise ValueError(f"{path}:{number}: invalid case: {exc}") from None
    return cases
