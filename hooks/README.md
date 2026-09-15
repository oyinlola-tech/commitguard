# Hook reference copies

These files are exactly what `commitguard install` writes, generated with an
empty interpreter path (so they fall back to `commitguard` on `PATH`). The
installer embeds the interpreter that ran `commitguard install` instead.

Do not copy them by hand; use `commitguard install`, which also preserves and
chains existing hooks. `tests/integration/hooks/test_hook_templates.py` fails
if these copies drift from the generator.
