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

# Install project dependencies first (cache layer). Defaults to public PyPI;
# override with --build-arg PIP_INDEX_URL=... for an internal mirror.
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt

# Install the package itself (no editable install in container). raas_mcp is
# fully self-contained (own raas_client.py, no SSEApiClient/vcf_salt needed).
COPY pyproject.toml .
COPY raas_mcp/ raas_mcp/
RUN pip install --no-cache-dir --no-deps .

# --------------------------------------------------------------------------- #
# Stage 2: runtime — minimal image                                            #
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="raas-mcp-server" \
      org.opencontainers.image.description="Salt RaaS MCP Server — Streamable HTTP transport" \
      org.opencontainers.image.source="https://github.com/saltstack/raas-mcp" \
      org.opencontainers.image.licenses="Apache-2.0"

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
