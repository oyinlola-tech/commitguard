#!/usr/bin/env bash
# Remove CommitGuard-managed Git hooks from the current repository.
#
# Thin wrapper around `commitguard uninstall`. Only hooks carrying the
# CommitGuard marker will ever be removed; preserved hooks are restored.
set -euo pipefail

if ! command -v commitguard >/dev/null 2>&1; then
    echo "error: commitguard is not on PATH (run scripts/install-dev.sh and activate .venv)" >&2
    exit 1
fi

exec commitguard uninstall "$@"
