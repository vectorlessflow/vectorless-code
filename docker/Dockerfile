# vectorless-code — Simplified Docker image for daemon-based architecture.
#
# The daemon now manages its own lifecycle (file watching, auto-restart),
# so Docker just needs to start it and keep the container running.

FROM python:3.12-slim

# Install runtime dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 vcc \
    && useradd -u 1000 -g 1000 -m vcc

# Install uv for fast package installation
RUN pip install --quiet uv

# Create runtime directory for daemon socket
RUN mkdir -p /var/vectorless_code \
    && chown -R vcc:vcc /var/vectorless_code

WORKDIR /workspace

# Runtime environment variables
ENV VCC_RUNTIME_DIR=/var/vectorless_code \
    VCC_DAEMON_SUPERVISED=1 \
    PYTHONUNBUFFERED=1

# Install vectorless-code
# Build mount for editable installs (faster iteration during dev)
# Production builds override with:
#   --build-arg VCC_INSTALL_SPEC=vectorless-code
ARG VCC_INSTALL_SPEC=""
RUN --mount=type=bind,source=.,target=/vcc-src,rw=true \
    if [ -z "$VCC_INSTALL_SPEC" ]; then \
        VCC_INSTALL_SPEC="/vcc-src[dev]"; \
    fi; \
    uv pip install --system "${VCC_INSTALL_SPEC}"

# Switch to vcc user
USER vcc

# Default: run the daemon (it will manage itself)
CMD ["python", "-m", "vectorless_code.daemon"]
