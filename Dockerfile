# --------------------------------------------------------------------------- #
# Stage 1: builder — install dependencies into a venv                        #
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for native wheel compilation (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create isolated venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install project dependencies first (cache layer)
COPY requirements.txt .
ARG PIP_INDEX_URL=https://packages.vcfd.broadcom.net/artifactory/api/pypi/saltstack-pypi-virtual/simple
RUN pip install --no-cache-dir -r requirements.txt

# Install the package itself (no editable install in container)
COPY pyproject.toml .
COPY raas_mcp/ raas_mcp/
RUN pip install --no-cache-dir --no-deps .

# Install SSEApiClient (provides the sseapiclient module used by vcf_salt.connection)
RUN pip install --no-cache-dir "SSEApiClient>=8.18.4.0,<9"

# Install vcf_salt from source — SSEApiClient wheel does NOT expose the vcf_salt
# module name, so we copy the local checkout which includes api_discovery.json
COPY vcf_salt_src/ /build/vcf_salt/
RUN cp -r /build/vcf_salt \
      "$(python -c 'import site; print(site.getsitepackages()[0])')/vcf_salt"

# --------------------------------------------------------------------------- #
# Stage 2: runtime — minimal image                                            #
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="raas-mcp-server" \
      org.opencontainers.image.description="Salt RaaS MCP Server — Streamable HTTP transport" \
      org.opencontainers.image.source="https://github.com/broadcom/salt"

# Non-root user
RUN groupadd -r raas && useradd -r -g raas -d /app -s /sbin/nologin raas

WORKDIR /app

# Copy the venv from builder stage
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime configuration via environment variables
# (see HttpServerConfig for the full list)
ENV MCP_PORT=8080 \
    METRICS_PORT=9090

EXPOSE ${MCP_PORT} ${METRICS_PORT}

USER raas

# Health check — uses the /health/live endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${MCP_PORT}/health/live')"

ENTRYPOINT ["raas-mcp-server"]
CMD ["--transport", "http"]
