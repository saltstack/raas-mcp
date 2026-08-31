# Architecture

## Design goals

- Expose the full RaaS RPC surface (~200 resource/method pairs) as MCP
  tools without hand-writing per-tool bindings — the catalog is generated
  from a bundled discovery document.
- Support two deployment shapes from one codebase: a local stdio process
  launched by an MCP client (Cursor, Claude Desktop), and a multi-tenant
  Streamable HTTP service deployable on Kubernetes or a VM. Both transports
  share the same catalog/validation/dispatch/approval-gate logic.
- Delegate every authorization decision to RaaS itself — raas-mcp never
  implements its own privilege model (see `SECURITY.md`).
- Have zero runtime dependency on anything outside public PyPI.

## Layers

### Catalog

`raas_mcp/catalog.py` builds one `CatalogEntry` (and one MCP `Tool`) per
`resource.method` pair in the bundled discovery document
(`raas_mcp/data/api_discovery.json`, loaded via `raas_mcp/discovery.py`).
Tool names are `{resource}_{method}` (e.g. `ret_get_minions`). Descriptions
come from `raas_mcp/help_text.py`, which turns RaaS's RPC docstrings into
concise tool descriptions and filters out Salt's generic `LoadedMod`
boilerplate.

### Dispatcher

`raas_mcp/dispatcher.py::dispatch()` is the single entry point both
transports call: strict parameter validation (reject unknown/missing keys
before any RaaS call), an optional approval-gate check (MCP elicitation),
then the actual RaaS call, returning a structured JSON success/error result
(`raas_mcp/errors.py`). On a RaaS 401/403 mid-call, it invalidates the
caller's opaque Bearer token (not applicable to VIDB JWT callers — see
below).

### RaaS client

`raas_mcp/raas_client.py` is the only module that talks to RaaS over the
network. It implements the RaaS RPC wire protocol directly on `httpx`:

- `RaasClient.login(server, user, password, ...)` — the username/password
  flow (XSRF cookie → `POST /account/login` → JWT → `POST /rpc` with
  `Authorization: JWT <jwt>`), used by stdio mode and the HTTP opaque-token
  path.
- `RaasClient.from_bearer(server, token, ...)` — VIDB JWT passthrough: skips
  the login flow, forwards the caller's own JWT unchanged as
  `Authorization: Bearer <token>` on every RPC call.
- `connect_from_mapping(mapping)` — a small compatibility shim so
  `dispatcher.py` / `auth/token_endpoint.py` don't need to know which
  construction path applies; it branches on an `auth_token` vs `auth` key.

Both constructors expose the same `client.api.<resource>.<method>(**kwargs)`
attribute-proxy interface, so the dispatcher's call site is identical
regardless of which auth path built the client.

### Auth (HTTP transport only)

Two independent Bearer-token paths, routed by
`raas_mcp/auth/verifier.py::DualModeTokenVerifier`:

1. **Opaque path** — `POST /token` (`auth/token_endpoint.py`) exchanges a
   caller's RaaS username/password for a short-lived, in-memory-only opaque
   token (`auth/token_store.py`). The server resolves the caller's stored
   credential on every subsequent call; the opaque token itself is never
   forwarded to RaaS.
2. **VIDB JWT path (VCF SSO)** — `auth/vidb_auth.py::VidbJwtValidator`
   discovers a tenant's JWKS via OIDC auto-discovery at startup and verifies
   incoming JWTs' signature/claims in place. No exchange step, no local
   token-store entry — the JWT's lifecycle belongs entirely to VIDB.

`auth/protected_resource.py` publishes both paths' discovery metadata at
`GET /.well-known/oauth-protected-resource` (RFC 9728).

### Transports

- `raas_mcp/server.py` — stdio MCP server. One shared `RaasClient` for the
  process lifetime, credentials from `~/.salt/config.yml`.
- `raas_mcp/server_http.py` — Streamable HTTP ASGI app. Builds a
  per-request `RaasClient` from the caller's resolved credential (opaque or
  VIDB), so every call runs under the caller's own RaaS privilege level —
  never a shared server-level identity.
- `raas_mcp/__main__.py` — `raas-mcp-server --transport {stdio,http}`.
  HTTP mode runs the MCP endpoint and the Prometheus `/metrics` endpoint on
  two separate Uvicorn servers concurrently.

## Statelessness and Kubernetes scaling

The HTTP transport holds two kinds of state, both in-memory-only and
per-pod: the opaque `TokenStore` and the MCP protocol session (negotiated
version, active tool-call context). In a multi-pod deployment this means:

- Callers using the **opaque token path** need Ingress sticky sessions
  (the Helm chart's `ingress.yaml` adds nginx affinity annotations
  automatically when `hpa.enabled=true`) — a token issued by one pod is not
  valid on another.
- Callers using the **VIDB JWT path exclusively** are truly stateless with
  respect to authentication — no `TokenStore` entry is ever created, so
  pods can be freely replaced or scaled without sticky sessions being
  required for auth correctness. MCP protocol session continuity still
  benefits from affinity on this path, but isn't required for correctness.

## Extension points

- New tools appear automatically when `raas_mcp/data/api_discovery.json` is
  regenerated from a newer RaaS release — no code change needed unless the
  RPC shape itself changes.
- Add typed convenience behavior to `raas_mcp/raas_client.py` rather than
  constructing ad-hoc RPC payloads elsewhere.
- Add new auth paths as another branch in `DualModeTokenVerifier` plus a
  corresponding `connect_from_mapping` case — don't special-case them in
  `dispatcher.py`.
