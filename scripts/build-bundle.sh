#!/usr/bin/env bash
# Build a self-contained offline bundle for raas-mcp-server.
#
# Output: dist/raas-mcp-server-bundle-<version>.tar.gz
#   ├── wheels/          ← all wheels (raas-mcp-server + vcf-salt + all deps)
#   ├── install.sh       ← single-command installer for the target machine
#   └── README-INSTALL.txt
#
# Usage:
#   ./scripts/build-bundle.sh
#
# Environment overrides:
#   PIP_INDEX_URL      override the Broadcom Artifactory index
#   PYTHON             python binary to use (default: python3.11)
#   VCF_SALT_PATH      path to vcf-salt source (default: ../vcf-salt)
#   INCLUDE_VENDOR     set to "1" to bundle SSEApiClient (default: 0)
#   TARGET_PLATFORM    download wheels for a different platform (default: current)
#                      Example for Linux x86-64 Python 3.11:
#                        TARGET_PLATFORM="manylinux_2_17_x86_64"
#                        TARGET_PYTHON_VERSION="311"
#                        TARGET_ABI="cp311"
#   TARGET_PYTHON_VERSION  e.g. "311" for Python 3.11
#   TARGET_ABI             e.g. "cp311" for CPython 3.11
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VCF_SALT="${VCF_SALT_PATH:-${ROOT}/../vcf-salt}"
INDEX_URL="${PIP_INDEX_URL:-https://packages.vcfd.broadcom.net/artifactory/api/pypi/saltstack-pypi-virtual/simple}"
PY="${PYTHON:-python3.11}"
INCLUDE_VENDOR="${INCLUDE_VENDOR:-0}"
TARGET_PLATFORM="${TARGET_PLATFORM:-}"
TARGET_PYTHON_VERSION="${TARGET_PYTHON_VERSION:-}"
TARGET_ABI="${TARGET_ABI:-}"

# Build --platform / --python-version / --abi flags for pip download
PLATFORM_FLAGS=()
if [[ -n "${TARGET_PLATFORM}" ]]; then
  PLATFORM_FLAGS+=(--platform "${TARGET_PLATFORM}" --only-binary :all:)
  [[ -n "${TARGET_PYTHON_VERSION}" ]] && PLATFORM_FLAGS+=(--python-version "${TARGET_PYTHON_VERSION}")
  [[ -n "${TARGET_ABI}" ]] && PLATFORM_FLAGS+=(--abi "${TARGET_ABI}")
fi

# Derive version from pyproject.toml
VERSION=$(python3 -c "
import re, pathlib
t = pathlib.Path('${ROOT}/pyproject.toml').read_text()
m = re.search(r'^version\s*=\s*\"([^\"]+)\"', t, re.M)
print(m.group(1) if m else '0.0.0')
")

DIST="${ROOT}/dist"
WHEELS="${DIST}/wheels"
BUNDLE_NAME="raas-mcp-server-bundle-${VERSION}"
BUNDLE_DIR="${DIST}/${BUNDLE_NAME}"

echo "==> Building raas-mcp-server ${VERSION} bundle"
echo "    vcf-salt:        ${VCF_SALT}"
echo "    wheels:          ${WHEELS}"
[[ -n "${TARGET_PLATFORM}" ]] && echo "    target platform: ${TARGET_PLATFORM} (py${TARGET_PYTHON_VERSION:-native} / ${TARGET_ABI:-native})"
echo ""

# ── 1. Clean / create output dirs ─────────────────────────────────────────────
# Always wipe wheels/ so stale platform-specific wheels from a previous run
# (e.g. a macOS build followed by a Linux build) don't pollute the bundle.
rm -rf "${BUNDLE_DIR}" "${WHEELS}"
mkdir -p "${WHEELS}"

# ── 2. Reuse (or create) the dev venv — it already has hatchling ──────────────
# We need hatchling+editables in the pip that builds the wheel; the dev venv
# was bootstrapped by setup-venv.sh so we use it here.  Avoid python -m build
# (which creates a fresh isolated env and tries to reach files.pythonhosted.org).
DEV_VENV="${ROOT}/.venv"
if [[ ! -x "${DEV_VENV}/bin/pip" ]]; then
  echo "==> Dev venv not found — creating minimal build venv"
  "$PY" -m venv "${DEV_VENV}"
  "${DEV_VENV}/bin/pip" install --index-url "${INDEX_URL}" -q hatchling editables
fi
PIP="${DEV_VENV}/bin/pip"

# ── 3. Build raas-mcp-server wheel (pure package, no deps) ────────────────────
echo "==> Building raas-mcp-server wheel"
# --no-build-isolation reuses the venv's already-installed hatchling/editables
"${PIP}" wheel \
  --no-build-isolation \
  --no-deps \
  --index-url "${INDEX_URL}" \
  --wheel-dir "${WHEELS}" \
  "${ROOT}"

# ── 4. Build vcf-salt wheel (pure package, no deps) ───────────────────────────
if [[ -d "${VCF_SALT}" ]]; then
  echo "==> Building vcf-salt wheel"
  "${PIP}" wheel \
    --no-build-isolation \
    --no-deps \
    --index-url "${INDEX_URL}" \
    --wheel-dir "${WHEELS}" \
    "${VCF_SALT}"
else
  echo "WARNING: vcf-salt not found at ${VCF_SALT} — skipping vcf-salt wheel"
fi

# ── 5. Download all runtime dependency wheels ─────────────────────────────────
echo "==> Downloading dependency wheels"
# Use the already-built raas-mcp-server wheel as the source so pip resolves its
# declared deps (mcp, pyyaml) rather than re-reading pyproject.toml.
RAAS_WHEEL=$(ls "${WHEELS}"/raas_mcp_server-*.whl 2>/dev/null | head -1)
if [[ -z "${RAAS_WHEEL}" ]]; then
  echo "ERROR: raas-mcp-server wheel not found in ${WHEELS}" >&2
  exit 1
fi

"${PIP}" download \
  --index-url "${INDEX_URL}" \
  --dest "${WHEELS}" \
  --no-deps \
  "${PLATFORM_FLAGS[@]+"${PLATFORM_FLAGS[@]}"}" \
  mcp pyyaml

# Download mcp's transitive deps (anyio, httpx, starlette, etc.)
"${PIP}" download \
  --index-url "${INDEX_URL}" \
  --dest "${WHEELS}" \
  "${PLATFORM_FLAGS[@]+"${PLATFORM_FLAGS[@]}"}" \
  "mcp>=1.27.0"

# ── 6. (Optional) Vendor SSEApiClient ─────────────────────────────────────────
if [[ "${INCLUDE_VENDOR}" == "1" ]]; then
  echo "==> Downloading SSEApiClient (vendor)"
  "${PIP}" download \
    --index-url "${INDEX_URL}" \
    --dest "${WHEELS}" \
    "${PLATFORM_FLAGS[@]+"${PLATFORM_FLAGS[@]}"}" \
    "SSEApiClient>=8.18.4.0,<9" || echo "WARNING: SSEApiClient not available from index — add manually"
fi

# ── 7. Assemble bundle directory ──────────────────────────────────────────────
echo "==> Assembling bundle"
mkdir -p "${BUNDLE_DIR}/wheels"
cp "${WHEELS}"/*.whl "${BUNDLE_DIR}/wheels/"

# install.sh for the target machine
cat > "${BUNDLE_DIR}/install.sh" << 'INSTALL_EOF'
#!/usr/bin/env bash
# Install raas-mcp-server from the offline bundle (no internet access required).
#
# Usage:
#   ./install.sh [--target /path/to/venv]
#   PYTHON=python3.11 ./install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEELS="${SCRIPT_DIR}/wheels"
PY="${PYTHON:-python3}"
VENV="${1:-${SCRIPT_DIR}/venv}"

echo "==> Creating venv at ${VENV}"
"$PY" -m venv "${VENV}"

echo "==> Installing wheels from bundle"
"${VENV}/bin/pip" install --no-index --find-links "${WHEELS}" --upgrade pip

# Install all wheels in dependency order via --find-links (pip resolves order)
"${VENV}/bin/pip" install \
  --no-index \
  --find-links "${WHEELS}" \
  raas_mcp_server

echo ""
echo "Installation complete."
echo "  Activate: source ${VENV}/bin/activate"
echo "  Run:      raas-mcp-server --help"
echo ""
echo "  If SSEApiClient was not bundled, install separately:"
echo "    ${VENV}/bin/pip install SSEApiClient-*.whl  (copy wheel first)"
INSTALL_EOF
chmod +x "${BUNDLE_DIR}/install.sh"

# README
cat > "${BUNDLE_DIR}/README-INSTALL.txt" << README_EOF
raas-mcp-server ${VERSION} — offline bundle
=============================================
Built on: $(uname -s) $(uname -m)
$(
  if [[ -n "${TARGET_PLATFORM}" ]]; then
    echo "Target platform: ${TARGET_PLATFORM} (py${TARGET_PYTHON_VERSION:-native} ${TARGET_ABI:-native})"
  else
    echo "Target platform: SAME AS BUILD MACHINE (see note below)"
  fi
)

Contents
--------
  wheels/           All Python wheels needed to run raas-mcp-server.
                    vcf-salt is included.  SSEApiClient may NOT be included
                    unless the bundle was built with INCLUDE_VENDOR=1.
  install.sh        Installer — creates a venv and installs from wheels/.
  README-INSTALL.txt  This file.

Quick install
-------------
  1.  Copy this directory to the target machine.
  2.  Run: ./install.sh
      (or: PYTHON=python3.11 ./install.sh /opt/raas-mcp-server-venv)
  3.  Configure: edit ~/.salt/config.yml with raas_url / auth / allowed_tools.
  4.  Register in Cursor: add the venv's raas-mcp-server binary to ~/.cursor/mcp.json.
  5.  Verify: /path/to/venv/bin/raas-mcp-server --help

Platform note
-------------
  Some wheels in wheels/ are platform-specific (pydantic_core, cryptography,
  pyyaml, cffi, rpds_py).  If the bundle was built without TARGET_PLATFORM set
  it matches the OS and CPU of the machine that ran build-bundle.sh.
  
  To build a Linux x86-64 bundle on macOS, re-run build-bundle.sh with:
    TARGET_PLATFORM=manylinux_2_17_x86_64 \
    TARGET_PYTHON_VERSION=311 \
    TARGET_ABI=cp311 \
    ./scripts/build-bundle.sh

  The pure-Python wheels (mcp, raas_mcp_server, vcf_salt, etc.) are universal
  and work on any platform.

SSEApiClient (optional)
-----------------------
  SSEApiClient provides the HTTP transport used by vcf-salt to reach RaaS.
  Without it, raas-mcp-server will still start but will fail on every tool call.

  To add it to the bundle:
    1.  Obtain the wheel from the Broadcom internal PyPI or your colleague.
    2.  Copy the .whl file into wheels/.
    3.  Re-run ./install.sh (it will pick it up automatically).

  Or install it after the fact:
    /path/to/venv/bin/pip install --no-index SSEApiClient-*.whl

Configuration
-------------
  See the README.md inside the installed package, or
  /path/to/venv/lib/python*/site-packages/raas_mcp/../../../share/...
  (or just read the README from the source tree at mops/salt/raas-mcp-server/README.md).
README_EOF

# ── 8. Create tarball ─────────────────────────────────────────────────────────
echo "==> Creating tarball: dist/${BUNDLE_NAME}.tar.gz"
cd "${DIST}"
tar czf "${BUNDLE_NAME}.tar.gz" "${BUNDLE_NAME}/"

echo ""
echo "✓  Bundle ready: dist/${BUNDLE_NAME}.tar.gz"
echo ""
echo "   Copy to target:  scp dist/${BUNDLE_NAME}.tar.gz user@host:~/"
echo "   Extract & run:   tar xzf ${BUNDLE_NAME}.tar.gz && ${BUNDLE_NAME}/install.sh"
