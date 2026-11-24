#!/usr/bin/env bash
# Install raas-mcp-server (HTTP transport) as a systemd service on Linux.
#
# Usage (as root):
#   sudo ./scripts/install-systemd.sh --raas-url https://salt-raas.example.com
#   sudo ./scripts/install-systemd.sh --uninstall [--purge]
#
# Options:
#   --raas-url URL     RaaS base URL (required on first install; written to the env file)
#   --port PORT        MCP/HTTP port           (default: 8080)
#   --metrics-port P   Prometheus /metrics port (default: 9090)
#   --python BIN       Python >= 3.11 to build the venv with (default: python3)
#   --source DIR       Project dir to install from (default: this repo checkout)
#   --pypi             Install raas-mcp-server from the configured pip index instead
#   --uninstall        Stop, disable and remove the unit and install dir
#   --purge            With --uninstall: also remove /etc/raas-mcp and the service user
#   -h, --help         Show this help
#
# Layout:
#   /opt/raas-mcp/venv               virtualenv containing raas-mcp-server
#   /etc/raas-mcp/raas-mcp.env       configuration (KEY=value; never overwritten once present)
#   /etc/systemd/system/raas-mcp.service
#
# The service holds NO RaaS credentials. In HTTP mode each caller supplies their
# own RaaS username/password (POST /token) or a VIDB JWT, so every call runs with
# that caller's RaaS privileges.
set -euo pipefail

SERVICE=raas-mcp
SVC_USER=raas-mcp
INSTALL_DIR=/opt/raas-mcp
CONF_DIR=/etc/raas-mcp
ENV_FILE="$CONF_DIR/raas-mcp.env"
UNIT_FILE="/etc/systemd/system/${SERVICE}.service"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAAS_URL=""
MCP_PORT=8080
METRICS_PORT=9090
PY=python3
SOURCE="$ROOT"
FROM_PYPI=0
UNINSTALL=0
PURGE=0

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --raas-url)     RAAS_URL="${2:?}"; shift 2 ;;
    --port)         MCP_PORT="${2:?}"; shift 2 ;;
    --metrics-port) METRICS_PORT="${2:?}"; shift 2 ;;
    --python)       PY="${2:?}"; shift 2 ;;
    --source)       SOURCE="${2:?}"; shift 2 ;;
    --pypi)         FROM_PYPI=1; shift ;;
    --uninstall)    UNINSTALL=1; shift ;;
    --purge)        PURGE=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

[[ "$(uname -s)" == "Linux" ]] || die "this script only supports Linux"
[[ $EUID -eq 0 ]] || die "run as root (e.g. with sudo)"
command -v systemctl >/dev/null || die "systemctl not found; systemd is required"

# ---------------------------------------------------------------- uninstall --
if [[ $UNINSTALL -eq 1 ]]; then
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT_FILE"
  systemctl daemon-reload
  rm -rf "$INSTALL_DIR"
  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$CONF_DIR"
    id "$SVC_USER" &>/dev/null && userdel "$SVC_USER" || true
  else
    echo "Kept $CONF_DIR and user '$SVC_USER' (use --purge to remove)."
  fi
  echo "✓ $SERVICE uninstalled"
  exit 0
fi

# ------------------------------------------------------------------ install --
if [[ ! -f "$ENV_FILE" && -z "$RAAS_URL" ]]; then
  die "--raas-url is required on first install"
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "$PY is missing or older than Python 3.11 (use --python)"
"$PY" -c 'import venv, ensurepip' 2>/dev/null \
  || die "python venv/ensurepip missing (Debian/Ubuntu: apt install python3-venv)"
if [[ $FROM_PYPI -eq 0 ]]; then
  [[ -f "$SOURCE/pyproject.toml" ]] || die "no pyproject.toml in $SOURCE (use --source or --pypi)"
fi

# Dedicated unprivileged service account
if ! id "$SVC_USER" &>/dev/null; then
  useradd --system --no-create-home --home-dir "$INSTALL_DIR" \
          --shell /usr/sbin/nologin "$SVC_USER"
fi

# Virtualenv + package
mkdir -p "$INSTALL_DIR"
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || "$PY" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
if [[ $FROM_PYPI -eq 1 ]]; then
  "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade raas-mcp-server
else
  "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade "$SOURCE"
fi
chown -R root:root "$INSTALL_DIR"

# Config (only created if absent, so re-running upgrades the code but keeps your settings)
mkdir -p "$CONF_DIR"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# raas-mcp-server configuration (systemd EnvironmentFile: plain KEY=value, no quotes/export).
# After editing:  systemctl restart $SERVICE
RAAS_URL=$RAAS_URL
MCP_PORT=$MCP_PORT
METRICS_PORT=$METRICS_PORT

# RAAS_INSECURE=false
# RAAS_TIMEOUT=60
# TOKEN_TTL_SECONDS=3600
# CORS_ALLOWED_ORIGINS=https://app.example.com
# ALLOWED_TOOLS=ret_*,minions_get_minion_cache
# APPROVAL_GATE=
#
# Direct TLS (otherwise terminate TLS at a reverse proxy; Basic credentials
# are sent to POST /token, so do NOT expose plain HTTP beyond localhost).
# TLS_ENABLED=true
# TLS_CERT_PATH=/etc/raas-mcp/tls/tls.crt
# TLS_KEY_PATH=/etc/raas-mcp/tls/tls.key
#
# VIDB / OIDC SSO (JWT passthrough):
# VIDB_ISSUER_URL=https://vidb.example.com/oidc
EOF
fi
chown root:"$SVC_USER" "$CONF_DIR" "$ENV_FILE"
chmod 0750 "$CONF_DIR"
chmod 0640 "$ENV_FILE"

# systemd unit
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=RaaS MCP Server (Streamable HTTP)
Documentation=https://github.com/saltstack/raas-mcp#readme
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/raas-mcp-server --transport http
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Hardening
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
LockPersonality=true
SystemCallArchitectures=native

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$UNIT_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

# Port from the env file (may differ from --port on re-runs)
PORT="$(sed -n 's/^MCP_PORT=//p' "$ENV_FILE" | tail -n1)"
PORT="${PORT:-8080}"
sleep 2
if systemctl is-active --quiet "$SERVICE"; then
  echo "✓ $SERVICE is running on port $PORT"
else
  echo "✗ $SERVICE failed to start; recent log:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2 || true
  exit 1
fi
cat <<EOF

Config:   $ENV_FILE   (edit, then: systemctl restart $SERVICE)
Logs:     journalctl -u $SERVICE -f
Health:   curl http://localhost:$PORT/health/live
Metrics:  curl http://localhost:$METRICS_PORT/metrics
EOF
