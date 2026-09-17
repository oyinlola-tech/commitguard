# Fuzzing, ReDoS and invariants

Three kinds of test that do not need a human to think of the input: property-based
fuzzing of every parser that touches untrusted data, timing every regular
expression against adversarial input, and asserting the engine's invariants over
generated configurations.

All of it runs in `pytest -m security` (350 tests) and in
`commitguard reproduce security`. **Recorded 2026-09-17.**

## Property-based fuzzing

25 properties, **7,476 generated examples** in the recorded run, derandomized by
default so a failure reproduces exactly.

| Target | Property |
|---|---|
| `parse_trailers` | never raises on any text; deterministic |
| The analyzer | an AI co-author trailer, however cased, spaced or prefixed, is **never** allowed |
| The analyzer | human trailers with list prefixes are never blocked |
| The analyzer | appending an AI co-author to arbitrary text always blocks |
| `normalize_text` | idempotent; nothing that renders as nothing survives |
| `parse_identity` | never raises; accepted emails contain no angle brackets |
| `parse_config`, `load_yaml` | arbitrary text and structured documents yield a valid config or a `CommitGuardError` - nothing else |
| `normalize_webhook` | mutated payloads for five event types raise only `WebhookValidationError` |
| `parse_json_object` | arbitrary bytes yield a dict or `WebhookValidationError` |
| `verify_signature` | only the exact signature is accepted; any body change is rejected |
| Validators (revision, SHA, identifier, config key, path) | raise only `UnsafeInputError`; accepted values satisfy the invariants callers rely on |
| Pagination parsers | raise only `InputValidationError`; cursors round-trip |
| `sanitize_for_terminal` | output has no control, C1 or bidi characters, and respects the length bound |
| `escape_markdown` | no unescaped markup or HTML survives |
| Redaction | a registered secret never survives; GitHub token shapes are removed |

**What it found:** two detection bypasses that hand-written cases had missed -
Unicode letters and numbers before a trailer key, and default-ignorable characters
inside a key or an agent alias. Both became dataset versions (1.2.0, 1.3.0), a
recorded failing run, a fix and a regression test. See
[detection-evaluation.md](detection-evaluation.md).

The strategy that found them is deliberately narrow: it generates the *same*
attribution with arbitrary casing, whitespace and any prefix character that is
neither an ASCII alphanumeric nor a Latin look-alike, and asserts the decision is
still BLOCK. A generic "throw random bytes at it" fuzzer would not have found
either.

## ReDoS

Every regular expression in `src/commitguard` is collected automatically (33
patterns: module-level, API route parameters, and the three written inline) and
run against **70 adversarial inputs of 50,000 characters** each - long runs of the
pattern's own character classes with a mismatching end, repeated prefixes,
separators that create word boundaries, and near-miss credential shapes.

Budget: **500 ms** per pattern. Worst case in the recorded run: **133.9 ms**.

| Pattern | Worst case |
|---|---|
| `research.results._SAFE` | 133.9 ms |
| `security.validation._GIT_CONFIG_KEY_RE` | 16.5 ms |
| `pagination.decode_cursor` (inline) | 15.7 ms |
| `security.secrets._DOTTED_RUN` | 12.6 ms |
| `notifications.preferences._EMAIL_RE` | 10.3 ms |

**What it found:** two quadratic patterns, both reachable from untrusted input.

1. **JWT redaction** (`\beyJ[A-Za-z0-9_-]{5,}\.…`): 80 KB of `eyJ-eyJ-…` took
   **4.6 seconds**. Redaction runs on log fields, audit data, notification content
   and Check Run output - and it runs **before** truncation, so a long attacker
   controlled string reaches it in full. Replaced by a linear scan: maximal dotted
   runs of base64url characters are matched once, then split without backtracking.
   320 KB now takes 34 ms.
2. **The workflow `secrets.` check** (`\$\{\{[^}]*\bsecrets\.`): 60 KB of
   `${{${{…` took **2.7 seconds**, and the App inspects workflow files fetched
   from monitored repositories, up to 512 KB. Replaced by two linear steps; 600 KB
   now takes 11 ms.

Equivalence was checked before either change was kept: the new redaction was
compared with the old pattern on 300,000 random strings, and never redacts less
(it redacts slightly more - a JWT preceded by a non-ASCII letter, which the old
`\b` missed).

A test also asserts that the inventory covers every inline regular expression in
the source, so a new one cannot be added without being measured.

## Invariants over generated inputs

| Invariant | How it is checked |
|---|---|
| Detection is deterministic | two independent analyzers produce identical reports |
| Decisions do not depend on detector order | registration order is shuffled |
| A mandatory policy can only make enforcement stricter | random repository and mandatory configurations; the effective action is never weaker than either |
| A mandatory policy never turns a block into an allow | the same, through the full analyzer, over generated commits |
| A mandatory policy cannot disable a policy | `enabled: false` is rejected |
| A detector that fails blocks | five exception types, every policy action: always BLOCK, and the result is marked incomplete |
| A finding with no policy blocks | an unknown rule id fails closed |

These are the assumptions the threat model relies on, tested as properties rather
than as examples.
