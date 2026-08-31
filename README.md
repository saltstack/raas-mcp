# raas-mcp-server

MCP server that exposes the Salt RaaS API as tools for AI agents (Cursor, Claude Desktop, etc.).

Each resource/method pair from the RaaS API discovery catalog becomes a single MCP tool —
no configuration needed beyond credentials to get 200+ Salt operations available to any
MCP-capable AI framework.

---

## Installation

raas-mcp is fully self-contained — it has no dependency on any other repo
checkout or internal-only package. Every runtime dependency
(`mcp`, `httpx`, `starlette`, `uvicorn`, `PyJWT`, ...) is on public PyPI.

### Prerequisites

- Python 3.11+

### Quick start

```bash
git clone https://github.com/saltstack/raas-mcp.git
cd raas-mcp

# 1. Create a virtual environment
python3.11 -m venv .venv

# 2. Install raas-mcp-server (+ dev/test extras)
.venv/bin/pip install -e '.[dev]'

# 3. Verify
.venv/bin/raas-mcp-server --help   # should exit 0 with usage text
```

Or use the convenience script:

```bash
./scripts/setup-venv.sh     # creates .venv and installs everything
```

---

## Configuration

All configuration lives in `~/.salt/config.yml` alongside your existing `vcf-salt` settings.

### Credential keys (shared with `vcf-salt`)

```yaml
# ~/.salt/config.yml
raas: https://salt-raas.example.com   # RaaS base URL
auth: "myuser:mypassword"             # USER:PASS for basic auth
config_name: default                  # optional profile name
timeout: 30                           # RaaS HTTP timeout in seconds
insecure: false                       # skip TLS verification (dev only)
```

Environment variable overrides: `RAASS_URL`, `RAASS_AUTH` (highest priority).

### MCP-specific keys

```yaml
# ~/.salt/config.yml

# Restrict which tools are exposed to AI agents (optional).
# Uses exact names or resource_* prefix globs.
# null (default) means all tools are exposed.
allowed_tools:
  - ret_*
  - tgt_*
  - cmd_route_cmd

# Require interactive operator confirmation before dispatching.
# Uses the same glob syntax as allowed_tools.
# Empty list (default) means no tools require confirmation.
approval_gate:
  - cmd_*
```

---

## Connecting to Cursor

Add the following to `~/.cursor/mcp.json` (create if absent):

```json
{
  "mcpServers": {
    "raas": {
      "command": "/absolute/path/to/raas-mcp/.venv/bin/raas-mcp-server",
      "args": [],
      "env": {}
    }
  }
}
```

Restart Cursor after saving. The MCP server list should now show **raas** with all exposed tools.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "raas": {
      "command": "/absolute/path/to/.venv/bin/raas-mcp-server"
    }
  }
}
```

---

## Example agent workflow

The following five-step workflow can be executed by any MCP-capable AI agent without
any extra scripting:

1. **Discover minions** — call `ret_get_minions` with no arguments
2. **Check connectivity** — call `cmd_route_cmd` with `{"tgt": "*", "fun": "test.ping"}`
3. **Apply a state** — call `cmd_route_cmd` with `{"tgt": "web-*", "fun": "state.apply", "arg": ["nginx"]}`
4. **Poll job status** — call `ret_get_jobs` with `{"jid": "<returned-jid>"}`
5. **Retrieve results** — call `ret_get_job` with `{"jid": "<jid>"}`

---

## Approval gate configuration

Tools matching patterns in `approval_gate` require interactive operator confirmation
before the RaaS call is dispatched.  Confirmation is requested via MCP elicitation
(Cursor v0.48+, Claude Desktop).

If your AI framework does not support MCP elicitation, the server returns
`APPROVAL_REQUIRED` and no action is taken.  Remove the tool from `approval_gate` or
switch to a framework that supports elicitation.

---

## Agent attribution warning

> **The MCP server does not track which AI agent session triggered a call.**
> RaaS records all dispatched calls by the configured `auth` user.
> For per-agent attribution, use your AI framework's session logging, or create
> a dedicated RaaS user per agent instance.

---

## HTTP Transport Mode (spec-010)

The server can run as a Kubernetes-deployable HTTP service that uses the
MCP 2025-03-26 Streamable HTTP transport.  Each MCP client authenticates
with its own RaaS credentials so that every tool call runs under the
caller's RaaS privilege level.

### Authentication flow

```
Client                         raas-mcp-server                  RaaS
  |                                   |                           |
  |  POST /token                      |                           |
  |  Authorization: Basic user:pass → |                           |
  |                                   |── api.get_versions() ────>|
  |                                   |<── 200 OK ────────────────|
  |<── {"access_token": "...", ...} ──|                           |
  |                                   |                           |
  |  POST /mcp                        |                           |
  |  Authorization: Bearer <token>  → |                           |
  |                                   |── tool call (user:pass) →|
  |<── MCP response ──────────────────|<── result ───────────────|
```

Key points:
- Credentials are **never** stored in plaintext — only in a short-lived
  in-memory `TokenStore` entry (default TTL: 1 hour).
- If RaaS returns 401/403 mid-call, the bearer token is immediately
  invalidated (FR-010).
- The `/metrics` endpoint is served on a **separate port** (default: 9090)
  and must not be exposed via the public Ingress.

### Environment variables (HTTP mode)

| Variable | Default | Description |
|---|---|---|
| `RAAS_URL` | (required) | RaaS base URL |
| `RAAS_INSECURE` | `false` | Skip TLS verification |
| `RAAS_TIMEOUT` | `60.0` | HTTP call timeout (seconds) |
| `MCP_PORT` | `8080` | Port for MCP/HTTP endpoint |
| `METRICS_PORT` | `9090` | Port for Prometheus /metrics |
| `TOKEN_TTL_SECONDS` | `3600` | Bearer token lifetime |
| `CORS_ALLOWED_ORIGINS` | (empty) | Comma-separated CORS origins |
| `KEEPALIVE_INTERVAL` | `15` | SSE keepalive interval (seconds) |
| `PRESTOP_DRAIN_SECONDS` | `15` | Pre-SIGTERM drain grace period |
| `TLS_ENABLED` | `false` | Direct TLS (prefer Ingress TLS) |
| `TLS_CERT_PATH` | (none) | Path to PEM certificate |
| `TLS_KEY_PATH` | (none) | Path to PEM private key |

### Running locally (HTTP mode)

```bash
export RAAS_URL=https://salt-raas.example.com
raas-mcp-server --transport http
```

### Kubernetes deployment (Helm)

```bash
# Add to your values.yaml
helm upgrade --install raas-mcp \
  ./helm/raas-mcp-server \
  --set config.raasUrl=https://salt-raas.example.com \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=mcp.example.com \
  --set ingress.hosts[0].paths[0].path=/ \
  --set ingress.hosts[0].paths[0].pathType=Prefix
```

> **⚠ Sticky sessions required with HPA** — when `hpa.enabled=true`, the
> Ingress template automatically adds nginx affinity annotations.  Without
> sticky sessions, `POST /mcp` requests from the same client may hit
> different pods, each with a different `TokenStore` (no shared state).

### Building the Docker image

```bash
./scripts/build-image.sh --tag v0.1.0 --registry harbor.example.com/salt --push
# With smoke test:
./scripts/build-image.sh --tag v0.1.0 --verify
```

### Observability

- `GET /metrics` (on `METRICS_PORT`) — Prometheus text format.
- Key metrics: `raas_mcp_active_sessions`, `raas_mcp_requests_total{status}`,
  `raas_mcp_token_issues_total`, `raas_mcp_token_invalidations_total`.

### RFC 9728 Protected Resource Metadata

```
GET /.well-known/oauth-protected-resource
```

Returns the token endpoint and supported scopes for MCP clients that use
OAuth resource server discovery.

---

## Running tests

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

---

---

## VCF SSO (VIDB JWT) Configuration

The MCP server supports **two authentication paths**:

| Path | How it works |
|---|---|
| **Opaque token** (default) | Client exchanges RaaS credentials at `POST /token` for a short-lived Bearer token |
| **VIDB JWT** (VCF SSO) | Client obtains a JWT directly from VIDB and presents it as a Bearer token without any exchange step |

### Enabling VIDB JWT authentication

Set `auth.vidb.issuerUrl` in your Helm values file:

```yaml
auth:
  vidb:
    issuerUrl: "https://vidb.vcf.example.com/oidc/<tenant-id>"
    jwksRefreshIntervalSeconds: 43200   # 12 h (default)
```

Or via environment variable:

```bash
VIDB_ISSUER_URL=https://vidb.vcf.example.com/oidc/<tenant-id>
VIDB_JWKS_REFRESH=43200
```

At startup the server performs OIDC discovery (`<issuerUrl>/.well-known/openid-configuration`) to locate the JWKS endpoint. If discovery fails the server starts in opaque-only mode and logs a `WARNING`.

### Helm values reference (VIDB fields)

| Helm value | Env var | Default | Description |
|---|---|---|---|
| `auth.vidb.issuerUrl` | `VIDB_ISSUER_URL` | `""` | VIDB OIDC issuer URL; empty = VIDB path disabled |
| `auth.vidb.jwksRefreshIntervalSeconds` | `VIDB_JWKS_REFRESH` | `43200` | JWKS cache TTL (300–86400 s) |

For a full setup walkthrough see `specs/010-mcp-remote-transport/quickstart.md` Section 5.

### VIDB troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `WARNING: VIDB OIDC discovery failed` at startup | `VIDB_ISSUER_URL` unreachable from pod | Check network policy; server falls back to opaque-token mode |
| VIDB JWT Bearer returns 401 at `/mcp` | Wrong `iss` claim (token was issued by a different VIDB tenant) | Ensure `VIDB_ISSUER_URL` matches the `iss` in the JWT |
| Overflow token (`ovl: true`) accepted but RaaS returns 403 | Roles are enforced by RaaS on each RPC, not at MCP auth layer | Verify the VIDB user has `vcf_salt_operations` permissions in RaaS |
| `authorization_servers` in `/.well-known/oauth-protected-resource` has only 1 entry | VIDB discovery failed at startup | Check startup logs for `VIDB OIDC discovery failed` warning |

---

## Project layout

```
raas-mcp/
├── raas_mcp/
│   ├── __init__.py          # package version
│   ├── auth/                # HTTP-mode auth (TokenStore, DualModeTokenVerifier, token_endpoint, protected_resource, vidb_auth)
│   ├── catalog.py           # builds MCP Tool list from api_discovery.json
│   ├── dispatcher.py        # validates params, checks approval gate, calls RaaS
│   ├── raas_client.py       # vendored httpx-based RaaS RPC client (no SSEApiClient)
│   ├── discovery.py         # loads the bundled api_discovery.json catalog
│   ├── config_file.py       # ~/.salt/config.yml loader (shared with vcf-salt)
│   ├── help_text.py         # tool description strings from RaaS RPC metadata
│   ├── redact.py            # credential redaction for error messages/logs
│   ├── data/
│   │   └── api_discovery.json  # bundled RaaS RPC catalog (~200 resource/method pairs)
│   ├── errors.py            # structured error/success result builders
│   ├── http_config.py       # HttpServerConfig + load() from env vars
│   ├── metrics.py           # Prometheus metrics + metrics_app
│   ├── server.py            # stdio MCP server entry point (spec-008)
│   ├── server_http.py       # Streamable HTTP ASGI app (spec-010)
│   ├── server_config.py     # operator config loader (credentials + MCP keys)
│   └── __main__.py          # python -m raas_mcp / raas-mcp-server --transport {stdio,http}
├── tests/                   # unit + integration tests (pytest, respx, httpx.ASGITransport)
├── helm/
│   └── raas-mcp-server/     # Helm chart (Chart.yaml, values.yaml, templates/)
├── scripts/
│   ├── build-image.sh       # Docker image builder with --verify smoke test
│   ├── check_release.py     # secret scan + version-match + smoke test (CI gate)
│   ├── setup-venv.sh
│   ├── setup-vscode-venv.sh
│   └── refresh-cursor-token.sh
├── .github/workflows/       # CI: lint+test matrix, release-check, docker, helm
├── docs/
│   ├── ARCHITECTURE.md
│   └── BUILDING.md
├── Dockerfile               # two-stage build (builder + runtime), public PyPI by default
├── .dockerignore
├── pyproject.toml
├── requirements.txt
├── pip.conf.example
├── LICENSE
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
└── README.md
```
