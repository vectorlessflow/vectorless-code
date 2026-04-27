# Single-stage image with cache-friendly layer ordering.
#
# Stable layers (reuse across releases):
#   1. apt install gosu + create vcc user
#   2. install uv
#   3. writable-path setup (mkdir /var/vectorless_code, chown to vcc)
#
# Per-release layers (invalidate when the source tree changes):
#   4. COPY . /vcc-src — build context
#   5. `uv pip install "vectorless-code"` — installs package + deps
#
# Uses glibc-based slim image — vectorless ships pre-built Rust wheels.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 vcc \
    && useradd -u 1000 -g 1000 -m vcc

RUN pip install --quiet uv

# Writable paths the daemon needs, pre-chowned to vcc.
RUN mkdir -p /var/vectorless_code \
    && chown -R vcc:vcc /var/vectorless_code

WORKDIR /workspace

# Runtime defaults — all overridable at `docker run -e ...` time.
ENV VECTORLESS_CODE_DIR=/workspace/.vectorless_code \
    VECTORLESS_CODE_RUNTIME_DIR=/var/vectorless_code \
    VECTORLESS_CODE_DAEMON_SUPERVISED=1

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# ─── Per-release layer (last so only this one invalidates per release) ─────
#
# Default (PyPI flow): install vectorless-code from PyPI.
# Release workflow / local tests override with:
#   --build-arg VCC_INSTALL_SPEC=/vcc-src
# to install from the source tree.
ARG VCC_INSTALL_SPEC=""
RUN --mount=type=bind,source=.,target=/vcc-src,rw=true \
    if [ -z "$VCC_INSTALL_SPEC" ]; then \
        VCC_INSTALL_SPEC="vectorless-code"; \
    fi; \
    uv pip install --system "${VCC_INSTALL_SPEC}"
