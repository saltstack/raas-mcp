# Contributing

Thank you for improving raas-mcp.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
pytest -q
ruff check .
python scripts/check_release.py
```

## Design expectations

- Keep the transport boundary clean: `raas_mcp/server.py` (stdio) and
  `raas_mcp/server_http.py` (Streamable HTTP) both dispatch through the same
  `raas_mcp/dispatcher.py::dispatch()` — don't duplicate catalog/validation/
  approval-gate logic between transports.
- `raas_mcp/raas_client.py` is the only place that talks to RaaS over the
  network. Add typed convenience behavior there rather than constructing
  ad-hoc RPC payloads elsewhere.
- Every RaaS credential path (opaque Bearer token, VIDB JWT passthrough,
  stdio config-file auth) must remain in-memory only — never persist a raw
  RaaS credential or JWT to disk.
- Tool names and parameter names are a stable contract once released
  (see `raas_mcp/catalog.py`); breaking a tool name requires a deprecation
  notice and a migration window, not a silent rename.
- Keep `raas_mcp` dependency-free of anything not on public PyPI. This
  project intentionally reimplements the RaaS RPC wire protocol on `httpx`
  (see `docs/ARCHITECTURE.md`) rather than depending on any internal-only
  package.
- Add a regression test for every safety boundary and reported bug —
  `tests/test_raas_client.py`'s VIDB-passthrough tests are a template.
- Keep stdio-mode and HTTP-mode behavior in parity: a change to one
  transport's auth/dispatch semantics needs an equivalent test in the other
  transport's test file (`test_server_integration.py` / `test_http_transport.py`).

## Pull requests

Include:

- the problem being solved and the affected transport(s) (stdio / HTTP / both);
- security/auth impact, if any (opaque-token, VIDB-JWT, or approval-gate behavior);
- tests executed;
- documentation updates for new tools, config keys, or Helm values.

Do not submit real RaaS credentials, customer target/minion data, internal
hostnames, or production configuration in examples, tests, or fixtures.
