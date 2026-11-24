# Connecting Claude Code to a raas-mcp HTTP server

This guide covers running `raas-mcp-server --transport http` on a Linux host
(systemd) and connecting **Claude Code** to it. For the server's
authentication internals see the "HTTP Transport Mode" and "OIDC / VIDB JWT SSO"
sections of the [README](../README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

Contents

1. [Install the server as a systemd service](#1-install-the-server-as-a-systemd-service)
2. [Enable HTTPS (self-signed)](#2-enable-https-self-signed)
3. [Connect Claude Code](#3-connect-claude-code)
   - [Option A: username/password via helper script (recommended)](#option-a-usernamepassword-via-helper-script-recommended)
   - [Option B: static Bearer token](#option-b-static-bearer-token)
   - [Option C: OAuth / OIDC SSO (VIDB)](#option-c-oauth--oidc-sso-vidb)
4. [Troubleshooting](#4-troubleshooting)

## How authentication works (30-second version)

The server holds **no RaaS identity of its own**. Every caller authenticates as
themselves and every RaaS call runs with that caller's privileges.

- `POST /token` accepts `Authorization: Basic base64(user:pass)`, verifies the
  credentials against RaaS and returns a short-lived opaque **Bearer** token
  (`TOKEN_TTL_SECONDS`, default 1 hour, kept in server memory only).
- `/mcp` accepts **only** `Authorization: Bearer ...` (that opaque token, or a
  VIDB JWT when SSO is enabled). It does **not** accept Basic auth, so a
  username/password cannot be placed directly in the client config - the client
  must first exchange them at `/token`.

## 1. Install the server as a systemd service

On the Linux host (root, Python >= 3.11, systemd):

```bash
git clone https://github.com/saltstack/raas-mcp.git && cd raas-mcp
sudo ./scripts/install-systemd.sh --raas-url https://<your-raas-host>
```

This creates a `raas-mcp` system user, a venv in `/opt/raas-mcp`, the config
file `/etc/raas-mcp/raas-mcp.env` (never overwritten on re-run) and the
`raas-mcp.service` unit. Useful options: `--port`, `--metrics-port`,
`--python`, `--pypi`, `--uninstall [--purge]`.

```bash
systemctl status raas-mcp
journalctl -u raas-mcp -f
curl http://localhost:8080/health/live        # -> {"status":"live"}
```

Open the MCP port in the host firewall (keep the metrics port 9090 closed):

```bash
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload
```

Edit `/etc/raas-mcp/raas-mcp.env` and `systemctl restart raas-mcp` to change
settings (see the README's environment-variable table).

> **RaaS with a self-signed certificate.** The server verifies RaaS's TLS
> certificate. If RaaS uses a self-signed cert you will see
> `CERTIFICATE_VERIFY_FAILED` in the journal, and `/token` returns 401 even
> with correct credentials. Either give RaaS a certificate with proper SANs
> (preferred) or, for a lab only, set `RAAS_INSECURE=true` in the env file.
> Trusting a cert via `SSL_CERT_FILE` only helps if its SANs match the
> hostname in `RAAS_URL`.

## 2. Enable HTTPS (self-signed)

`/token` receives your password, so don't run plain HTTP beyond localhost.
Prefer a TLS-terminating reverse proxy or a CA-signed certificate; for a lab, a
self-signed certificate works:

```bash
HOST=raas-mcp.example.com          # DNS name clients will use
IP=10.0.0.10                       # server IP
D=/etc/raas-mcp/tls
sudo mkdir -p $D
sudo openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 825 \
  -keyout $D/tls.key -out $D/tls.crt -subj "/CN=$HOST" \
  -addext "subjectAltName=DNS:$HOST,IP:$IP"
sudo chown -R root:raas-mcp $D
sudo chmod 750 $D && sudo chmod 640 $D/tls.key && sudo chmod 644 $D/tls.crt

sudo tee -a /etc/raas-mcp/raas-mcp.env >/dev/null <<EOF2
TLS_ENABLED=true
TLS_CERT_PATH=$D/tls.crt
TLS_KEY_PATH=$D/tls.key
EOF2
sudo systemctl restart raas-mcp
```

Only the MCP port is TLS; the metrics port stays plain HTTP. Copy
`/etc/raas-mcp/tls/tls.crt` to each client machine (it is a public
certificate) - the client steps below use it as the "CA file".

## 3. Connect Claude Code

### Option A: username/password via helper script (recommended)

Claude Code's `headersHelper` setting runs a command each time it
(re)connects and uses the JSON headers it prints. The helper
[`scripts/claude-code-headers.sh`](../scripts/claude-code-headers.sh) exchanges
your RaaS username/password for a fresh Bearer token at `/token`, so the
password never appears in the Claude config and the hourly token expiry is
handled automatically.

1. Install the helper and your config file (replace the values):

   ```bash
   mkdir -p ~/.config/raas-mcp
   cp scripts/claude-code-headers.sh ~/.config/raas-mcp/headers.sh
   chmod 700 ~/.config/raas-mcp/headers.sh
   cp /path/to/tls.crt ~/.config/raas-mcp/ca.crt        # only for a self-signed server cert

   cat > ~/.config/raas-mcp/credentials <<EOF
   RAAS_USER=your-raas-username
   RAAS_PASSWORD=your-raas-password
   RAAS_MCP_URL=https://raas-mcp.example.com:8080
   RAAS_MCP_CA=$HOME/.config/raas-mcp/ca.crt
   EOF
   chmod 600 ~/.config/raas-mcp/credentials
   ```

   The file is parsed, not sourced, so special characters in the password are
   safe; the helper refuses to run if the file is readable by others. Omit
   `RAAS_MCP_CA` when the server uses a publicly trusted certificate.

2. Test the helper (should print `{"Authorization": "Bearer ..."}`):

   ```bash
   ~/.config/raas-mcp/headers.sh
   ```

3. Register the server (user scope shown; use `-s project` or `-s local` as needed):

   ```bash
   claude mcp add-json -s user raas-http '{
     "type": "http",
     "url": "https://raas-mcp.example.com:8080/mcp",
     "headersHelper": "'"$HOME"'/.config/raas-mcp/headers.sh"
   }'
   ```

4. **Self-signed server certificate only:** Claude Code itself must also trust
   the certificate. Add to your shell profile (e.g. `~/.zshrc`) and start
   Claude Code from a new shell:

   ```bash
   export NODE_EXTRA_CA_CERTS="$HOME/.config/raas-mcp/ca.crt"
   ```

5. Run `/mcp` inside Claude Code (or `claude mcp get raas-http`); the server
   should show **Connected**.

Several RaaS instances can coexist: give each its own server name, credentials
file and helper copy (set `RAAS_MCP_CREDENTIALS` in a small wrapper, or keep one
copy of the script per instance with its own config file path).

### Option B: static Bearer token

Simplest, but the token expires after `TOKEN_TTL_SECONDS` and is invalidated by
a server restart, so you must repeat it when that happens:

```bash
TOKEN=$(curl -s -u 'user:password' --cacert ca.crt -X POST \
  https://raas-mcp.example.com:8080/token | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

claude mcp add --transport http -s user raas-http \
  https://raas-mcp.example.com:8080/mcp -H "Authorization: Bearer $TOKEN"
```

(Typing the password on the command line leaves it in shell history; prefer
Option A.)

### Option C: OAuth / OIDC SSO (VIDB)

Use this when users sign in through a browser with your identity provider (VIDB)
instead of typing a RaaS password.

**Server side** - set the issuer and restart:

```bash
echo 'VIDB_ISSUER_URL=https://idp.example.com/oidc/<tenant-id>' | sudo tee -a /etc/raas-mcp/raas-mcp.env
sudo systemctl restart raas-mcp
journalctl -u raas-mcp | grep -i vidb      # expect "VIDB JWT path enabled"
```

The server validates the JWT signature and claims (JWKS via OIDC discovery) and
forwards the **same JWT** to RaaS as `Authorization: Bearer <jwt>`; RaaS
enforces the user's roles. The opaque-token path keeps working alongside it.
Tokens need a `vcf_salt_operations` entry in `authorization_details` (unless
VIDB marked them `ovl: true`). See the README's VIDB section and Helm values.

**Client side** - the client must obtain a VIDB-issued JWT and send it as a
Bearer token. Two ways:

- *Static / scripted:* obtain the JWT with your organization's OIDC login tool
  and register it with `-H "Authorization: Bearer <jwt>"` (as in Option B), or
  make a `headersHelper` script print `{"Authorization": "Bearer <jwt>"}` after
  running that login. VIDB JWTs are short-lived, so a helper is more practical.
- *Claude Code's built-in browser OAuth:* when `/mcp` returns 401, Claude Code
  reads `/.well-known/oauth-protected-resource`, which lists the VIDB issuer as
  an authorization server once VIDB is enabled, and starts a browser sign-in.
  This requires VIDB to accept Claude Code as an OAuth client (Dynamic Client
  Registration, or a pre-registered client via
  `claude mcp add ... --client-id <id> --callback-port <port>`). This path has
  **not** been validated against a real VIDB tenant in this repo - confirm with
  your VIDB administrator.

> **Why the browser prompt fails without VIDB.** With only the opaque-token
> path, the metadata points at `/token`, but raas-mcp is *not* an OAuth
> authorization server (no `/register`, `/authorize` or
> `/.well-known/oauth-authorization-server`). Claude Code's OAuth fallback then
> fails with `Dynamic Client Registration rejected (HTTP 404)`. That error
> means "no valid Authorization header was sent" - use Option A or B, or
> enable VIDB.

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/mcp` shows `Dynamic Client Registration rejected (HTTP 404)` and opens a browser | Helper missing/failing, so Claude Code fell back to OAuth | Run the helper by hand and fix its error; check `claude mcp get raas-http` |
| `ECONNREFUSED` | Port closed or service down | `systemctl status raas-mcp`, `ss -ltnp \| grep 8080`, open the firewall port |
| `ENOTFOUND` | DNS | On macOS `nslookup` bypasses VPN per-domain resolvers; test with `dscacheutil -q host -a name <host>` or `ping` |
| `DEPTH_ZERO_SELF_SIGNED_CERT` / `unable to verify` in Claude Code | Server cert not trusted by Claude Code | Set `NODE_EXTRA_CA_CERTS` (step 4 of Option A) and restart Claude Code |
| Helper: `token request ... failed: HTTP 401` | Wrong credentials **or** server cannot verify RaaS's TLS cert | `journalctl -u raas-mcp`; see the RaaS self-signed note in section 1 |
| Helper: `CERTIFICATE_VERIFY_FAILED` | Helper does not trust the server cert | Set `RAAS_MCP_CA` to the server's `tls.crt` |
| Helper: `... is accessible by other users` | Credentials file permissions | `chmod 600 ~/.config/raas-mcp/credentials` |
| Tools stop working after ~1 hour (static token) | Token expired | Use Option A, or reissue the token |
