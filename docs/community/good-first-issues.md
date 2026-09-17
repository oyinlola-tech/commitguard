# Good first issues

Real gaps, scoped so that a newcomer can finish one in an evening. These are
**drafts for the maintainer to open as issues**, not existing issues - the
repository has none open today.

Each says where to look and how to know you are done.

## 1. `commitguard doctor` does not check the global configuration file

`docs/configuration.md` documents `~/.config/commitguard/config.yaml`, and
`doctor` reports the layers that apply, but it does not say whether that file
exists or is readable when it is absent.

- Files: `src/commitguard/cli/commands/doctor.py`, `config/loader.py`
- Done when: `doctor` shows the global configuration path with `PASS` or
  `NOT CONFIGURED`, and a test covers both.

## 2. Add a `--quiet` option to `commitguard install`

`check` has `--quiet`; `install` always prints several lines, which is noise in
setup scripts.

- Files: `src/commitguard/cli/commands/install.py`
- Done when: `--quiet` prints nothing on success, still prints errors, and a test
  asserts empty stdout.

## 3. Detection rule contribution: a new AI agent

Rules are data, not code (`rules/ai-identities.yaml`). Adding an agent needs its
exact identity evidence, not a guess.

- Files: `rules/ai-identities.yaml`, `tests/unit/rules/`
- Done when: the agent's canonical trailer is blocked, a near-miss human name is
  not, and both are added to the labelled dataset as a **new version**.

## 4. Improve the error when `.commitguard.yaml` has a typo in a policy name

Today an unknown policy id is rejected by schema validation; the message could
name the closest valid id.

- Files: `src/commitguard/config/schema.py`, `config/loader.py`
- Done when: `ai_coauthors:` suggests `ai_coauthor`, and a test asserts the
  suggestion. Keep the exit code 2.

## 5. A platform benchmark scenario for tags

`src/commitguard/research/platform.py` covers commits, pushes, configuration and
uninstall, but not pushing a tag, which takes a different code path in the
pre-push hook.

- Files: `src/commitguard/research/platform.py`
- Done when: the check appears in `commitguard benchmark platform` output with a
  clear expected/observed pair.

## 6. Documentation: a page on writing a detection rule

There is no single page for "I want CommitGuard to detect my organization's
internal agent".

- Files: new `docs/writing-rules.md`, linked from `docs/detection-engine.md`
- Done when: someone can add a rule and a test by following it, and the example
  in it is verified by `tests/integration/test_examples.py`.

## 7. `commitguard scan` progress for very large ranges

Scanning 100,000 commits takes about 44 seconds (measured) with no output until
it finishes.

- Files: `src/commitguard/cli/commands/scan.py`
- Done when: a progress indicator appears only on a TTY, never in JSON output,
  and a test asserts JSON output is unchanged.

Before starting one, say so in the issue so two people do not do the same work.
Read [CONTRIBUTING.md](../../CONTRIBUTING.md) first - especially the part about
this repository's own commit policy: **a commit with an AI co-author trailer will
be rejected by CommitGuard's own check.**
