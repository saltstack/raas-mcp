#!/usr/bin/env bash
# Create .venv for raas-mcp-server and install from the Broadcom internal PyPI mirror.
#
# Usage:
#   ./scripts/setup-venv.sh
#
# Environment overrides:
#   PIP_INDEX_URL  — override the Broadcom Artifactory index
#   PYTHON         — python binary to use (default: python3.11)
#   VCF_SALT_PATH  — path to vcf-salt source for editable install (default: ../vcf-salt)
#   RAAS_MCP_EXTRAS — comma-separated extras, e.g. "vendor,test"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INDEX_URL="${PIP_INDEX_URL:-https://packages.vcfd.broadcom.net/artifactory/api/pypi/saltstack-pypi-virtual/simple}"
PY="${PYTHON:-python3.11}"
VCF_SALT="${VCF_SALT_PATH:-${ROOT}/../vcf-salt}"

extras="${RAAS_MCP_EXTRAS:-test}"
editable="${ROOT}[${extras}]"

test -x "$ROOT/.venv/bin/python" || "$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --index-url "$INDEX_URL" -U pip

# Install vcf_salt as an editable dependency so raas_mcp can import it.
if [[ -d "$VCF_SALT" ]]; then
  "$ROOT/.venv/bin/pip" install --index-url "$INDEX_URL" -e "$VCF_SALT"
else
  echo "WARNING: vcf-salt not found at $VCF_SALT — install it manually before running raas-mcp-server" >&2
fi

"$ROOT/.venv/bin/pip" install --index-url "$INDEX_URL" -e "$editable"

echo ""
echo "✓  venv ready at $ROOT/.venv"
echo "   Activate: source $ROOT/.venv/bin/activate"
echo "   Run:      raas-mcp-server"
