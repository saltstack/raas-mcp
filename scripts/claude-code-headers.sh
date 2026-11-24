#!/usr/bin/env bash
# Claude Code `headersHelper` for a raas-mcp HTTP server.
#
# Exchanges your RaaS username/password for a short-lived MCP Bearer token
# (POST /token) and prints it as a JSON headers object, which is what Claude
# Code expects from a headersHelper. Claude Code runs this on every (re)connect,
# so expired tokens are replaced automatically and no password ever lands in
# your Claude config.
#
# Configuration lives in one file (default ~/.config/raas-mcp/credentials,
# override with $RAAS_MCP_CREDENTIALS). chmod 600. Plain KEY=VALUE lines,
# parsed - never executed:
#   RAAS_USER=alice                              (required)
#   RAAS_PASSWORD=s3cret                         (required)
#   RAAS_MCP_URL=https://raas-mcp.example.com:8080   (required; server base URL)
#   RAAS_MCP_CA=/home/alice/.config/raas-mcp/ca.crt  (optional; trust a self-signed cert)
#
# RAAS_MCP_URL / RAAS_MCP_CA may also be set as environment variables, which
# take precedence over the file.
set -euo pipefail
export RAAS_MCP_URL="${RAAS_MCP_URL:-}"
export RAAS_MCP_CA="${RAAS_MCP_CA:-}"
export RAAS_MCP_CREDENTIALS="${RAAS_MCP_CREDENTIALS:-$HOME/.config/raas-mcp/credentials}"

exec python3 -I - <<'PY'
import base64, json, os, ssl, sys, urllib.request

def fail(msg):
    print(f"raas-mcp headers helper: {msg}", file=sys.stderr)
    sys.exit(1)

creds = {}
path = os.environ["RAAS_MCP_CREDENTIALS"]
try:
    mode = os.stat(path).st_mode & 0o077
    if mode:
        fail(f"{path} is accessible by other users; run: chmod 600 {path}")
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        creds[k.strip()] = v
except OSError as e:
    fail(f"cannot read credentials file: {e}")
if not creds.get("RAAS_USER") or not creds.get("RAAS_PASSWORD"):
    fail(f"RAAS_USER / RAAS_PASSWORD missing in {path}")

url = (os.environ["RAAS_MCP_URL"] or creds.get("RAAS_MCP_URL", "")).rstrip("/")
if not url:
    fail(f"RAAS_MCP_URL is not set (env or {path})")
ca = os.environ["RAAS_MCP_CA"] or creds.get("RAAS_MCP_CA") or None

basic = base64.b64encode(f"{creds['RAAS_USER']}:{creds['RAAS_PASSWORD']}".encode()).decode()
req = urllib.request.Request(url + "/token", method="POST",
                             headers={"Authorization": f"Basic {basic}"})
ctx = ssl.create_default_context(cafile=ca)
try:
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        token = json.load(resp)["access_token"]
except Exception as e:
    fail(f"token request to {url}/token failed: {e}")
print(json.dumps({"Authorization": f"Bearer {token}"}))
PY
