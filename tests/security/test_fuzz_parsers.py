"""Property-based fuzzing of every parser and validator that handles untrusted input.

Untrusted input reaches CommitGuard as commit messages and identities (anyone who
can push), configuration files (anyone who can open a pull request), webhook
bodies and headers (anyone who can reach the endpoint), API query parameters
(signed-in users) and workflow files (repository contributors). For each entry
point the properties are:

* it never raises anything but its documented error type (an unexpected
  ``KeyError`` or ``TypeError`` would become an HTTP 500 or a crashed hook);
* what it accepts satisfies the invariants later code relies on;
* security-relevant results cannot be changed by disguising the input.

Example counts are recorded as evidence (see tests/security/conftest.py).
"""

import contextlib
import json
import string
import unicodedata
from typing import Any

import pytest
import yaml
from hypothesis import assume, given
from hypothesis import strategies as st

from commitguard.config.loader import parse_config
from commitguard.controlplane.errors import InputValidationError
from commitguard.controlplane.pagination import (
    MAX_CURSOR_CHARS,
    MAX_LIMIT,
    decode_cursor,
    encode_cursor,
    offset_cursor,
    parse_int_id,
    parse_limit,
    parse_search,
    parse_timestamp,
    sha_prefix,
)
from commitguard.core.decision import Action
from commitguard.exceptions.base import CommitGuardError, UnsafeInputError
from commitguard.git.commit import Commit
from commitguard.github.errors import WebhookValidationError
from commitguard.github.events import normalize_webhook
from commitguard.github.identifiers import split_full_name
from commitguard.github.markdown import escape_markdown
from commitguard.github.webhooks import compute_signature, parse_json_object, verify_signature
from commitguard.policies.defaults import default_policy_set
from commitguard.provenance.author import Identity, parse_identity
from commitguard.provenance.normalization import is_latin_lookalike, normalize_text
from commitguard.provenance.trailers import MAX_LEADING_CHARACTERS, parse_trailers
from commitguard.security.safe_yaml import load_yaml
from commitguard.security.sanitization import sanitize_for_terminal
from commitguard.security.secrets import REDACTED, Secret, SecretRedactor
from commitguard.security.validation import (
    validate_git_config_key,
    validate_git_sha,
    validate_identifier,
    validate_repository_path,
    validate_revision,
)
from commitguard.services.analysis import Analyzer

ANALYZER = Analyzer.create(default_policy_set())
HUMAN = Identity(name="Ada Lovelace", email="ada@example.com")
CLAUDE = "Claude <noreply@anthropic.com>"
KNOWN_POLICIES = {
    "ai_coauthor",
    "ai_identity",
    "ai_trailer",
    "bot_identity",
    "malformed_trailer",
}
HUMAN_TRAILERS = [
    "Signed-off-by: Ada Lovelace <ada@example.com>",
    "Reviewed-by: Grace Hopper <grace.hopper@example.org>",
    "Co-authored-by: Alan Turing <alan@turing.example>",
    "Acked-by: Katherine Johnson <kj@example.net>",
]

one_line = st.text(max_size=120).filter(
    lambda s: not any(c in s for c in "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
)
json_scalars = (
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=40)
)
json_values = st.recursive(
    json_scalars,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4)
    ),
    max_leaves=20,
)


def analyze(message: str) -> Action:
    return ANALYZER.analyze(Commit(author=HUMAN, committer=HUMAN, message=message)).action


# --------------------------------------------------------------------------- #
# Commit messages
# --------------------------------------------------------------------------- #
@given(st.text(max_size=2000))
def test_trailer_parser_never_raises_and_is_deterministic(evidence, message: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("parse_trailers: arbitrary text")
    first = parse_trailers(message)
    assert first == parse_trailers(message)
    for trailer in first.trailers:
        assert trailer.line_number >= 1
        assert trailer.key


@st.composite
def disguised_key(draw: st.DrawFn) -> str:
    key = "Co-authored-by"
    return "".join(ch.upper() if draw(st.booleans()) else ch.lower() for ch in key)


# Characters that can precede a key without being a letter of it: anything that is not an
# ASCII letter or digit (which would change the key itself) and not a look-alike of one (a
# disguised key character, covered by the detection dataset).
prefix_characters = st.characters(
    blacklist_categories=("Cs", "Cc", "Zl", "Zp"),
).filter(lambda ch: not (ch.isascii() and ch.isalnum()) and not is_latin_lookalike(ch))


@given(
    subject=one_line,
    body=st.text(max_size=300),
    key=disguised_key(),
    before_colon=st.text(" \t", max_size=3),
    after_colon=st.text(" \t", max_size=3),
    prefix=st.text(prefix_characters, max_size=MAX_LEADING_CHARACTERS),
    before=st.lists(st.sampled_from(HUMAN_TRAILERS), max_size=3),
    after=st.lists(st.sampled_from(HUMAN_TRAILERS), max_size=3),
)
def test_ai_coauthor_attribution_cannot_be_disguised_into_an_allow(  # type: ignore[no-untyped-def]
    evidence,
    subject: str,
    body: str,
    key: str,
    before_colon: str,
    after_colon: str,
    prefix: str,
    before: list[str],
    after: list[str],
) -> None:
    """Casing, spacing, symbols or non-ASCII letters before the key never hide attribution.

    Found two bypasses when first run (Phase 10): Unicode letters and numbers before
    the key (U+32AC, U+2460) - fixed in provenance/trailers.py, dataset 1.2.0.
    """
    evidence.count("analyzer: disguised AI co-author trailer")
    trailer = f"{prefix}{key}{before_colon}:{after_colon}{CLAUDE}"
    message = "\n\n".join(part for part in (subject, body) if part) + "\n\n"
    message += "\n".join([*before, trailer, *after]) + "\n"
    assert analyze(message) is Action.BLOCK


@given(
    subject=one_line,
    prefix=st.sampled_from(["", "- ", "* ", "> ", "\u2022 ", "\u2460 ", "#"]),
    trailers=st.lists(st.sampled_from(HUMAN_TRAILERS), min_size=1, max_size=5),
)
def test_human_trailers_are_never_blocked(  # type: ignore[no-untyped-def]
    evidence, subject: str, prefix: str, trailers: list[str]
) -> None:
    evidence.count("analyzer: human trailers with list prefixes")
    lowered = subject.casefold()
    assume("claude" not in lowered)
    assume("copilot" not in lowered)
    message = f"fix: {subject}\n\n" + "\n".join(prefix + t for t in trailers) + "\n"
    assert analyze(message) is not Action.BLOCK


@given(body=st.text(max_size=400))
def test_appending_an_ai_coauthor_always_blocks(evidence, body: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("analyzer: AI co-author appended to arbitrary text")
    assert analyze(f"{body}\n\nCo-authored-by: {CLAUDE}\n") is Action.BLOCK


@given(st.text(max_size=300))
def test_normalization_is_idempotent_and_removes_invisible_characters(evidence, text: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("normalize_text")
    once = normalize_text(text)
    assert normalize_text(once) == once
    # Nothing that renders as nothing survives: control, format and default-ignorable
    # characters (unassigned code points are kept; they render as a replacement glyph).
    for char in once:
        assert char == " " or unicodedata.category(char) not in ("Cc", "Cf", "Zl", "Zp")
        assert normalize_text("a" + char + "b") != "ab"


@given(st.text(max_size=300))
def test_identity_parser_never_raises(evidence, value: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("parse_identity")
    parsed = parse_identity(value)
    if parsed.email is not None:
        assert "<" not in parsed.email
        assert ">" not in parsed.email


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@given(st.text(max_size=500))
def test_configuration_text_is_accepted_or_rejected_cleanly(evidence, text: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("parse_config: arbitrary text")
    try:
        config = parse_config(text)
    except CommitGuardError:
        return
    assert config.version == 1


config_documents = st.fixed_dictionaries(
    {},
    optional={
        "version": st.sampled_from([1, 2, "1", True, None, 1.0]),
        "policies": st.dictionaries(
            st.sampled_from(
                [
                    "ai_coauthor",
                    "ai_identity",
                    "ai_trailer",
                    "bot_identity",
                    "malformed_trailer",
                    "nope",
                ]
            ),
            st.fixed_dictionaries(
                {},
                optional={
                    "enabled": st.sampled_from([True, False, "false", None, 0]),
                    "action": st.sampled_from(["block", "warn", "allow", "BLOCK", "deny", None]),
                },
            )
            | json_values,
            max_size=6,
        ),
        "enforcement": json_values,
        "extra": json_values,
    },
)


@given(config_documents)
def test_structured_configuration_is_accepted_or_rejected_cleanly(
    evidence, document: dict[str, Any]
) -> None:  # type: ignore[no-untyped-def]
    evidence.count("parse_config: structured documents")
    try:
        config = parse_config(yaml.safe_dump(document))
    except CommitGuardError:
        return
    assert config.version == 1
    assert set(config.policies) <= KNOWN_POLICIES


def test_yaml_alias_expansion_is_rejected() -> None:
    def level(index: int) -> str:
        name, previous = chr(98 + index), chr(97 + index)
        references = ", ".join([f"*{previous}"] * 4)
        return f"{name}: &{name} [{references}]\n"

    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(level(i) for i in range(20))
    with pytest.raises(yaml.YAMLError):
        load_yaml(bomb)


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #
SHA_A, SHA_B = "a" * 40, "b" * 40
REPOSITORY = {"id": 42, "name": "api", "full_name": "octo-org/api", "owner": {"login": "octo-org"}}
SEEDS: dict[str, dict[str, Any]] = {
    "push": {
        "ref": "refs/heads/main",
        "before": SHA_A,
        "after": SHA_B,
        "repository": REPOSITORY,
        "installation": {"id": 7},
    },
    "pull_request": {
        "action": "synchronize",
        "number": 3,
        "pull_request": {
            "number": 3,
            "base": {"sha": SHA_A, "ref": "main", "repo": REPOSITORY},
            "head": {"sha": SHA_B, "ref": "feature", "repo": REPOSITORY},
        },
        "repository": REPOSITORY,
        "installation": {"id": 7},
    },
    "merge_group": {
        "action": "checks_requested",
        "merge_group": {
            "head_sha": SHA_B,
            "head_ref": "refs/heads/gh-readonly-queue/main/pr-3-" + "e" * 40,
            "base_sha": SHA_A,
            "base_ref": "refs/heads/main",
        },
        "repository": REPOSITORY,
        "installation": {"id": 7},
    },
    "installation": {
        "action": "created",
        "installation": {
            "id": 7,
            "account": {"id": 1, "login": "octo-org", "type": "Organization"},
        },
        "repositories": [{"id": 42, "full_name": "octo-org/api"}],
    },
    "check_run": {
        "action": "rerequested",
        "check_run": {"id": 9, "name": "commitguard-app", "head_sha": SHA_B, "app": {"id": 5}},
        "repository": REPOSITORY,
        "installation": {"id": 7},
    },
}


def _paths(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    paths = [prefix] if prefix else []
    if isinstance(value, dict):
        for key, child in value.items():
            paths += _paths(child, (*prefix, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths += _paths(child, (*prefix, index))
    return paths


@st.composite
def mutated_payload(draw: st.DrawFn) -> tuple[str, Any]:
    event = draw(st.sampled_from(sorted(SEEDS)))
    payload = json.loads(json.dumps(SEEDS[event]))
    for _ in range(draw(st.integers(0, 4))):
        paths = _paths(payload)
        if not paths:
            break
        path = draw(st.sampled_from(paths))
        parent = payload
        for step in path[:-1]:
            parent = parent[step]
        if draw(st.booleans()) and isinstance(parent, dict):
            del parent[path[-1]]
        else:
            parent[path[-1]] = draw(
                json_values | st.sampled_from([SHA_A, "../x", "-x", "a" * 5000, 2**64, -1])
            )
    return draw(
        st.sampled_from([event, event, "ping", "check_suite", "installation_repositories"])
    ), payload


def test_webhook_seeds_are_valid() -> None:
    for event, payload in SEEDS.items():
        normalize_webhook(event, payload)


@given(mutated_payload())
def test_webhook_normalization_only_raises_validation_errors(
    evidence, sample: tuple[str, Any]
) -> None:  # type: ignore[no-untyped-def]
    evidence.count("normalize_webhook: mutated payloads")
    event, payload = sample
    with contextlib.suppress(WebhookValidationError):
        normalize_webhook(event, payload)


@given(st.binary(max_size=500) | json_values.map(lambda v: json.dumps(v).encode()))
def test_webhook_body_parser_only_raises_validation_errors(evidence, body: bytes) -> None:  # type: ignore[no-untyped-def]
    evidence.count("parse_json_object")
    with contextlib.suppress(WebhookValidationError):
        assert isinstance(parse_json_object(body), dict)


@given(
    body=st.binary(max_size=200),
    header=st.text(max_size=80),
    secret=st.text(min_size=16, max_size=40),
)
def test_only_the_exact_signature_is_accepted(
    evidence, body: bytes, header: str, secret: str
) -> None:  # type: ignore[no-untyped-def]
    evidence.count("verify_signature")
    key = Secret(secret)
    valid = compute_signature(key, body)
    verify_signature(key, body, valid)
    assume(header.strip() != valid)
    with pytest.raises(WebhookValidationError):
        verify_signature(key, body, header)
    tampered = body + b"x"
    with pytest.raises(WebhookValidationError):
        verify_signature(key, tampered, valid)


# --------------------------------------------------------------------------- #
# Identifiers, paths and API parameters
# --------------------------------------------------------------------------- #
@given(st.text(max_size=300))
def test_git_input_validators(evidence, value: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("security.validation")
    for validate in (
        validate_revision,
        validate_git_sha,
        validate_identifier,
        validate_git_config_key,
    ):
        try:
            accepted = validate(value)
        except UnsafeInputError:
            continue
        assert accepted == value
        assert not value.startswith("-")
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    try:
        path = validate_repository_path(value)
    except UnsafeInputError:
        return
    parts = path.split("/")
    assert ".." not in parts
    assert "" not in parts
    assert not path.startswith("/")
    assert "\\" not in path


@given(st.text(max_size=120))
def test_repository_full_names(evidence, value: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("split_full_name")
    try:
        owner, name = split_full_name(value)
    except UnsafeInputError:
        return
    assert f"{owner}/{name}" == value
    assert name not in (".", "..")
    assert all(c in string.ascii_letters + string.digits + "._-" for c in name)


@given(st.text(max_size=120) | st.none())
def test_api_parameter_parsers_only_raise_input_errors(evidence, raw: str | None) -> None:  # type: ignore[no-untyped-def]
    evidence.count("controlplane.pagination")
    for parse in (
        lambda: parse_limit(raw),
        lambda: offset_cursor(raw),
        lambda: decode_cursor(raw, (int, str)),
        lambda: parse_search(raw),
        lambda: parse_int_id(raw, "id"),
        lambda: parse_timestamp(raw, "since"),
        lambda: sha_prefix(raw),
    ):
        with contextlib.suppress(InputValidationError):
            parse()
    limit = None
    with contextlib.suppress(InputValidationError):
        limit = parse_limit(raw)
    assert limit is None or 1 <= limit <= MAX_LIMIT


@given(st.lists(st.integers(-(2**53), 2**53) | st.text(max_size=20), max_size=4))
def test_cursor_round_trip(evidence, values: list[int | str]) -> None:  # type: ignore[no-untyped-def]
    evidence.count("encode_cursor/decode_cursor")
    cursor = encode_cursor(values)
    assume(len(cursor) <= MAX_CURSOR_CHARS)
    assert decode_cursor(cursor, [type(v) for v in values]) == values


# --------------------------------------------------------------------------- #
# Output encoding
# --------------------------------------------------------------------------- #
@given(st.text(max_size=400), st.integers(20, 300), st.booleans())
def test_terminal_sanitization_removes_control_and_bidi_characters(  # type: ignore[no-untyped-def]
    evidence, text: str, limit: int, keep_newlines: bool
) -> None:
    evidence.count("sanitize_for_terminal")
    out = sanitize_for_terminal(text, max_length=limit, keep_newlines=keep_newlines)
    assert len(out) <= limit
    for char in out:
        if char == "\n" and keep_newlines:
            continue
        assert ord(char) >= 0x20
        assert not 0x7F <= ord(char) <= 0x9F
        assert char not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f"


@given(st.text(max_size=300))
def test_markdown_escaping_prevents_markup(evidence, text: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("escape_markdown")
    out = escape_markdown(text, limit=400)
    for character in "<>|":
        assert character not in out
    for index, char in enumerate(out):
        if char in "[]()`*_":
            assert index > 0
            assert out[index - 1] == "\\"


@given(
    secret=st.text(string.ascii_letters + string.digits + "-_", min_size=8, max_size=40),
    before=st.text(max_size=60),
    after=st.text(max_size=60),
)
def test_registered_secrets_never_survive_redaction(
    evidence, secret: str, before: str, after: str
) -> None:  # type: ignore[no-untyped-def]
    evidence.count("SecretRedactor.redact")
    assume(secret not in REDACTED)
    redactor = SecretRedactor()
    redactor.register(secret)
    out = redactor.redact(before + secret + after)
    assert secret not in out
    assert REDACTED in out or secret not in before + secret + after


@given(
    token=st.text(string.ascii_letters + string.digits, min_size=36, max_size=40),
    before=st.text(max_size=40),
)
def test_github_token_shapes_are_redacted(evidence, token: str, before: str) -> None:  # type: ignore[no-untyped-def]
    evidence.count("redact: GitHub token shapes")
    assume(not before[-1:].isalnum() and before[-1:] != "_")
    out = SecretRedactor().redact(f"{before}ghs_{token} trailing")
    assert token not in out
