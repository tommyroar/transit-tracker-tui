# =============================================================================
# Transit Tracker — multi-stage Docker build
# Produces a slim runtime image with only production dependencies.
# =============================================================================

# --------------- build stage ---------------
FROM python:3.13-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests and README (referenced in pyproject.toml)
COPY pyproject.toml uv.lock README.md ./

# Copy source code
COPY src/ src/

# Install production deps into a virtual-env.
# --no-dev  excludes dev dependency group
# --frozen  uses the lockfile as-is
# --no-editable  installs the package properly (not as editable)
# --no-install-package  skips macOS-only packages that won't build on Linux
RUN uv sync --no-dev --frozen --no-editable \
    --no-install-package pyobjc-framework-quartz \
    --no-install-package pyobjc-framework-cocoa \
    --no-install-package pyobjc-core \
    --no-install-package rumps

# --------------- runtime stage ---------------
FROM python:3.13-slim

# Create non-root user
RUN groupadd -g 1000 transit && \
    useradd -u 1000 -g transit -m transit

WORKDIR /app

# Copy the virtual-env and installed package from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Create data directory at the path _PROJECT_ROOT resolves to when installed.
# web.py uses Path(__file__).parent.parent.parent.parent / "data" which resolves
# to .venv/lib/data/ for a non-editable install.
RUN mkdir -p /app/.venv/lib/data && \
    chown transit:transit /app/.venv/lib/data

# Copy data files to both the expected path and /app/data for reference
COPY data/needle_stops.yaml /app/.venv/lib/data/needle_stops.yaml

# Copy entrypoint
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Ensure the venv python is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Ports: 8000 = WebSocket, 8080 = HTTP
EXPOSE 8000 8080

USER transit

ENTRYPOINT ["/app/entrypoint.sh"]
