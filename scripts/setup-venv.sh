#!/usr/bin/env bash
# Create .venv for raas-mcp-server and install it (fully self-contained —
# no other repo checkout is required).
#
# Usage:
#   ./scripts/setup-venv.sh
#
# Environment overrides:
#   PIP_INDEX_URL   — override the default public PyPI index (e.g. for an
#                      internal mirror; see pip.conf.example)
#   PYTHON          — python binary to use (default: python3.11)
#   RAAS_MCP_EXTRAS — comma-separated extras, e.g. "dev,test"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3.11}"
extras="${RAAS_MCP_EXTRAS:-dev}"
editable="${ROOT}[${extras}]"

INDEX_ARGS=()
if [[ -n "${PIP_INDEX_URL:-}" ]]; then
  INDEX_ARGS=(--index-url "${PIP_INDEX_URL}")
fi

test -x "$ROOT/.venv/bin/python" || "$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install "${INDEX_ARGS[@]}" -U pip
"$ROOT/.venv/bin/pip" install "${INDEX_ARGS[@]}" -e "${editable}"

echo ""
echo "✓  venv ready at $ROOT/.venv"
echo "   Activate: source $ROOT/.venv/bin/activate"
echo "   Run:      raas-mcp-server"
