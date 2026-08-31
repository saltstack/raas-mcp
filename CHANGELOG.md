# Changelog

## Unreleased

- Extracted from the `mops` monorepo (`mops/salt/raas-mcp-server`) into a
  standalone repository.
- Replaced the `vcf_salt`/`SSEApiClient` runtime dependency with a vendored,
  self-contained `httpx`-based RaaS RPC client (`raas_mcp/raas_client.py`).
  raas-mcp no longer requires an internal-only package or an internal PyPI
  mirror to build or run.
- Fixed a latent bug where the VIDB JWT passthrough path
  (`connect_from_mapping({"auth_token": ...})`) silently dropped the JWT and
  never actually forwarded it to RaaS. Covered by a regression test in
  `tests/test_raas_client.py`.
- Added public-repo guardrails: `LICENSE` (Apache-2.0), `SECURITY.md`,
  `CONTRIBUTING.md`, `docs/ARCHITECTURE.md`, `docs/BUILDING.md`, and CI
  (lint + test matrix, release checks, Docker build validation, Helm
  validation).

## 0.1.0

- Initial implementation: stdio MCP server exposing the RaaS API catalog as
  MCP tools (spec 008), plus a Streamable HTTP transport for Kubernetes/VM
  deployment with opaque-token and VIDB JWT (OIDC SSO) authentication (spec
  010).
