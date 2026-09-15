#!/usr/bin/env bash
# Install CommitGuard Git hooks into the current repository.
#
# Thin wrapper around `commitguard install` so there is exactly one
# implementation of hook installation. Existing hooks are preserved and chained.
set -euo pipefail

if ! command -v commitguard >/dev/null 2>&1; then
    echo "error: commitguard is not on PATH (run scripts/install-dev.sh and activate .venv)" >&2
    exit 1
fi

exec commitguard install "$@"
