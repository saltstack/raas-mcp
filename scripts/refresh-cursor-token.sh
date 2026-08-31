#!/usr/bin/env bash
# refresh-cursor-token.sh
#
# Gets a fresh Bearer token from the local raas-mcp-server and patches
# .cursor/mcp.json so Cursor can authenticate immediately.
#
# Usage:
#   ./scripts/refresh-cursor-token.sh                  # uses default server URL
#   ./scripts/refresh-cursor-token.sh http://localhost:18080  # custom port
#
# Requires: curl, python3, jq (or python3 fallback)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_ROOT="$(cd "${PROJECT_ROOT}/../../.." && pwd)"
MCP_JSON="${WORKSPACE_ROOT}/.cursor/mcp.json"
ENV_FILE="${WORKSPACE_ROOT}/../vcf-salt/.env.raas.local"
SERVER_URL="${1:-http://localhost:8080}"

# Load credentials
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "ERROR: Credentials file not found: ${ENV_FILE}" >&2
    echo "       Create it from: mops/salt/vcf-salt/.env.raas.example" >&2
    exit 1
fi
RAAS_AUTH="$(grep '^RAAS_AUTH=' "${ENV_FILE}" | cut -d= -f2-)"
if [[ -z "${RAAS_AUTH}" ]]; then
    echo "ERROR: RAAS_AUTH not found in ${ENV_FILE}" >&2
    exit 1
fi

# Check server is up
if ! curl -sf "${SERVER_URL}/health/live" >/dev/null 2>&1; then
    echo "ERROR: Server not reachable at ${SERVER_URL}" >&2
    echo "       Start it first: Run & Debug → 'RaaS MCP Server (HTTP, local)'" >&2
    exit 1
fi

# Get token
echo "==> Fetching token from ${SERVER_URL}/token ..."
TOKEN=$(curl -s -X POST "${SERVER_URL}/token" \
    -H "Authorization: Basic $(echo -n "${RAAS_AUTH}" | base64)" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['access_token'])")

if [[ -z "${TOKEN}" ]]; then
    echo "ERROR: Token request failed (check RaaS credentials)" >&2
    exit 1
fi

echo "==> Token: ${TOKEN:0:20}... (valid 1 hour)"

# Patch .cursor/mcp.json using python3 (no jq required)
python3 - "${MCP_JSON}" "${TOKEN}" <<'PYEOF'
import sys, json, pathlib

path = pathlib.Path(sys.argv[1])
token = sys.argv[2]

data = json.loads(path.read_text())
server = data.setdefault("mcpServers", {}).setdefault("raas-streamable-http", {})
server["headers"] = {"Authorization": f"Bearer {token}"}

path.write_text(json.dumps(data, indent=2) + "\n")
print(f"==> Updated {path}")
PYEOF

echo ""
echo "✓ Done. Reload MCP in Cursor: Cursor Settings → MCP → refresh raas-streamable-http"
echo "  (or restart Cursor if the server list doesn't update)"
