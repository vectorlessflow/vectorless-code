"""vcc runtime paths and connection helpers.

Lightweight module for runtime artifacts (socket, PID, log).
No vectorless_code dependency so CLI can import without full daemon stack.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _runtime_dir() -> Path:
    """Return the vcc runtime directory.

    Holds daemon.sock, daemon.pid, daemon.log. Kept separate from
    user-settings dir so that (e.g. in Docker) the socket can live on
    the container's native filesystem while settings live on a bind mount.

    Override with VCC_RUNTIME_DIR. Defaults to platform-specific cache dir.
    """
    override = os.environ.get("VCC_RUNTIME_DIR")
    if override:
        return Path(override)

    # Platform-specific cache directory
    if sys.platform == "darwin":
        # macOS: ~/Library/Caches/vectorless-code/
        return Path.home() / "Library" / "Caches" / "vectorless-code"
    elif sys.platform == "win32":
        # Windows: %LOCALAPPDATA%\vectorless-code\
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "vectorless-code"
        return Path.home() / ".vectorless-code"
    else:
        # Linux: ~/.cache/vectorless-code/ (XDG_CACHE_HOME)
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            return Path(xdg_cache) / "vectorless-code"
        return Path.home() / ".cache" / "vectorless-code"


def daemon_socket_path() -> str:
    """Return the daemon Unix socket or named pipe address."""
    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        import hashlib

        # Hash the runtime dir to produce unique pipe names,
        # preventing conflicts between different daemon instances
        dir_hash = hashlib.md5(str(runtime_dir).encode()).hexdigest()[:12]
        return rf"\\.\pipe\vcc_{dir_hash}"
    return str(runtime_dir / "daemon.sock")


def daemon_pid_path() -> Path:
    """Return the path for the daemon's PID file."""
    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the path for the daemon's log file."""
    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / "daemon.log"


def connection_family() -> str:
    """Return the connection family for this platform."""
    return "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
