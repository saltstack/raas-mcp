#!/usr/bin/env bash
# build-image.sh — build (and optionally push / verify) the raas-mcp-server Docker image.
#
# Usage:
#   ./scripts/build-image.sh [options]
#
# Options:
#   --tag <tag>           Image tag (default: git short SHA or "dev")
#   --registry <url>      Registry prefix, e.g. harbor.example.com/salt (default: empty)
#   --base-image <img>    Python base image override (default: python:3.12-slim)
#   --push                Push the image to the registry after building
#   --verify              Run a smoke-test: start a container and hit /health/live
#   --help                Show this help
#
# Environment variables:
#   PIP_INDEX_URL         PyPI mirror for pip inside the build stage
#                         (default: Broadcom internal PyPI)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
TAG="${TAG:-$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || echo "dev")}"
REGISTRY=""
BASE_IMAGE="python:3.12-slim"
PUSH=false
VERIFY=false
PIP_INDEX_URL="${PIP_INDEX_URL:-https://packages.vcfd.broadcom.net/artifactory/api/pypi/saltstack-pypi-virtual/simple}"

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)        TAG="$2";        shift 2 ;;
        --registry)   REGISTRY="$2";  shift 2 ;;
        --base-image) BASE_IMAGE="$2"; shift 2 ;;
        --push)       PUSH=true;       shift ;;
        --verify)     VERIFY=true;     shift ;;
        --help)
            sed -n '/^# Usage:/,/^[^#]/p' "$0" | head -n -1 | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# --------------------------------------------------------------------------- #
# Compose image name
# --------------------------------------------------------------------------- #
IMAGE_NAME="raas-mcp-server"
if [[ -n "${REGISTRY}" ]]; then
    FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${TAG}"
else
    FULL_IMAGE="${IMAGE_NAME}:${TAG}"
fi

echo "==> Building image: ${FULL_IMAGE}"
echo "    Base image:      ${BASE_IMAGE}"
echo "    PIP_INDEX_URL:   ${PIP_INDEX_URL}"

# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
docker build \
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --tag "${FULL_IMAGE}" \
    --file "${PROJECT_ROOT}/Dockerfile" \
    "${PROJECT_ROOT}"

echo "==> Build complete: ${FULL_IMAGE}"

# --------------------------------------------------------------------------- #
# Push (optional)
# --------------------------------------------------------------------------- #
if [[ "${PUSH}" == "true" ]]; then
    echo "==> Pushing ${FULL_IMAGE} ..."
    docker push "${FULL_IMAGE}"
    echo "==> Push complete."
fi

# --------------------------------------------------------------------------- #
# Verify (optional): start a container and poll /health/live
# --------------------------------------------------------------------------- #
if [[ "${VERIFY}" == "true" ]]; then
    CONTAINER_NAME="raas-mcp-verify-$$"
    MCP_PORT=18080
    echo "==> Verifying image (smoke test on port ${MCP_PORT}) ..."

    # Start in background with a dummy RAAS_URL (health endpoint doesn't need RaaS)
    docker run --rm -d \
        --name "${CONTAINER_NAME}" \
        -p "${MCP_PORT}:8080" \
        -e "RAAS_URL=http://raas.test" \
        "${FULL_IMAGE}" \
        --transport http &>/dev/null

    HEALTHY=false
    for i in $(seq 1 10); do
        sleep 2
        if curl -sf "http://localhost:${MCP_PORT}/health/live" >/dev/null 2>&1; then
            HEALTHY=true
            break
        fi
        echo "    Waiting for container to be healthy (attempt ${i}/10) ..."
    done

    docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true

    if [[ "${HEALTHY}" == "true" ]]; then
        echo "==> Verification passed: /health/live returned 200."
    else
        echo "==> ERROR: Container did not become healthy within timeout." >&2
        exit 1
    fi
fi

echo "==> Done."
