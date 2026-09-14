#!/usr/bin/env bash
# Create a local virtual environment and install CommitGuard with dev extras.
#
# Usage: scripts/install-dev.sh            (uses $PYTHON or python3)
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
venv_dir="${repo_root}/.venv"

if ! "${python_bin}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "error: Python 3.12+ is required (found: $("${python_bin}" --version 2>&1))" >&2
    exit 1
fi

if [[ ! -d "${venv_dir}" ]]; then
    "${python_bin}" -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -m pip install --upgrade pip
"${venv_dir}/bin/python" -m pip install -e "${repo_root}[dev]"

echo
echo "Done. Activate with: source .venv/bin/activate"
echo "Then run: pytest && ruff check . && mypy"
