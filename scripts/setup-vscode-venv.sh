#!/usr/bin/env bash
# Create raas-mcp-server/.venv and install all deps for VS Code / local dev.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VCF_SALT="${ROOT}/../vcf-salt"
INDEX_URL="${PIP_INDEX_URL:-https://packages.vcfd.broadcom.net/artifactory/api/pypi/saltstack-pypi-virtual/simple}"

cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate

python -m pip install --index-url "${INDEX_URL}" -U pip hatchling editables >/dev/null

# Install vcf_salt as an editable dependency so raas_mcp can import it.
if [[ -d "${VCF_SALT}" ]]; then
  python -m pip install --index-url "${INDEX_URL}" -e "${VCF_SALT}"
else
  echo "WARNING: vcf-salt not found at ${VCF_SALT} — install it manually." >&2
fi

python -m pip install --index-url "${INDEX_URL}" --no-build-isolation -e '.[test]'

# Optional SSEApiClient wheel (provides the RaaS HTTP transport)
SSE_WHEEL="${RAASS_SSE_WHEEL:-${HOME}/Downloads/SSEApiClient-8.18.4.0-py3-none-any.whl}"
if [[ -f "${SSE_WHEEL}" ]]; then
  python -m pip install -q "${SSE_WHEEL}"
  echo "Installed SSEApiClient from ${SSE_WHEEL}"
else
  echo "Optional SSEApiClient wheel not found at ${SSE_WHEEL} (set RAASS_SSE_WHEEL to override)."
fi

echo "raas-mcp-server venv ready at ${ROOT}/.venv"
