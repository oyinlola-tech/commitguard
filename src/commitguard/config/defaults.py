"""Configuration file locations, limits and the ``commitguard init`` template."""

CONFIG_FILENAMES: tuple[str, ...] = (".commitguard.yaml", ".commitguard.yml")
DEFAULT_CONFIG_FILENAME = CONFIG_FILENAMES[0]
GLOBAL_CONFIG_DIRNAME = "commitguard"
GLOBAL_CONFIG_FILENAME = "config.yaml"
CURRENT_CONFIG_VERSION = 1

#: Refuse to parse configuration files larger than this (defence against
#: resource exhaustion via YAML alias expansion or huge files).
MAX_CONFIG_BYTES = 64 * 1024

DEFAULT_CONFIG_TEMPLATE = """\
# CommitGuard repository configuration.
# Docs: docs/configuration.md
#
# Policies not listed here keep their built-in secure defaults.
# Valid actions: allow | warn | block

version: 1

policies:
  ai_coauthor:
    enabled: true
    action: block
  ai_identity:
    enabled: true
    action: block
  ai_trailer:
    enabled: true
    action: block
  malformed_trailer:
    enabled: true
    action: warn
  bot_identity:
    enabled: true
    action: warn

# Which Git hooks enforce the policies above (after `commitguard install`).
# Disabling a hook is visible in `commitguard doctor`.
enforcement:
  pre_commit: true
  commit_msg: true
  pre_push: true
"""
