# Security policy

## Reporting

Report suspected vulnerabilities privately to the project maintainers. Do
not include real RaaS credentials, Bearer tokens, VIDB JWTs, or production
logs in a public issue.

## Threat model: the confused-deputy problem

raas-mcp is a **proxy**: it holds (or forwards) credentials that let it act
on RaaS on a caller's behalf. Its main security job is making sure a caller
can never do more than their own RaaS privilege allows, and that its own
Bearer-token layer never becomes a way to impersonate someone else. Mitigations:

- **No privilege elevation**: all RaaS authorization decisions are made by
  RaaS itself. raas-mcp does not implement its own privilege-level mapping
  or shadow authorization logic (`raas_mcp/dispatcher.py`).
- **No credential passthrough for opaque tokens**: an MCP-server-issued
  opaque Bearer token is never forwarded to RaaS. The server resolves the
  caller's stored RaaS credential server-side and dispatches with that.
- **Short-lived, in-memory-only tokens**: opaque Bearer tokens
  (`raas_mcp/auth/token_store.py`) default to a 1-hour TTL, live only in
  process memory, and are never written to disk. They do not survive a
  server restart.
- **Immediate invalidation on RaaS auth failure**: if RaaS rejects a stored
  credential mid-call (e.g. password rotated), the opaque Bearer token is
  invalidated immediately, forcing re-authentication.
- **VIDB JWT passthrough is stateless**: when a caller authenticates via VCF
  SSO (a VIDB-issued JWT), no local token-store entry is ever created — the
  JWT is verified in-place (JWKS signature check) and forwarded unchanged to
  RaaS on every call. Its lifecycle is managed entirely by VIDB, not by
  raas-mcp.

## Credential handling

- raas-mcp never persists a raw RaaS username/password, opaque Bearer
  token, or VIDB JWT to disk.
- stdio mode reads one operator-level credential from `~/.salt/config.yml`
  (or `VCF_SALT_CONFIG`) for the lifetime of the process — the same file
  and precedence chain used by `vcf-salt`.
- HTTP mode never stores a server-level RaaS credential in a ConfigMap,
  Secret, or Helm value (`FR-019`); every request carries its own
  credentials (an opaque Bearer token or a VIDB JWT).
- Error messages are passed through `raas_mcp/redact.py` before being
  returned to the caller or logged, to strip `user:pass@` URL forms.

## Network / transport

- `/metrics` is served on a separate port and must never be exposed through
  a public Ingress — it carries no authentication.
- CORS is disabled by default; when enabled, the allow-list is explicit
  origins only, `Access-Control-Allow-Credentials` is always `false`.
- Direct pod-level TLS is opt-in; the default deployment model terminates
  TLS at the Ingress. Plain HTTP to the outside world is never a supported
  configuration.

## Supported versions

Security fixes are applied to the latest release line. Upgrade before
reporting behavior from an older release.
