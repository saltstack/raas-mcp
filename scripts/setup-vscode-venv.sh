#!/usr/bin/env bash
# Create raas-mcp-server/.venv and install all deps for VS Code / local dev.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT"

INDEX_ARGS=()
if [[ -n "${PIP_INDEX_URL:-}" ]]; then
  INDEX_ARGS=(--index-url "${PIP_INDEX_URL}")
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate

python -m pip install "${INDEX_ARGS[@]}" -U pip hatchling editables >/dev/null
python -m pip install "${INDEX_ARGS[@]}" --no-build-isolation -e '.[dev,test]'

echo "raas-mcp-server venv ready at ${ROOT}/.venv"
