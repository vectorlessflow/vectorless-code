"""Daemon runtime paths and connection family."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _runtime_dir() -> Path:
    """Return the daemon runtime directory."""
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


def daemon_runtime_dir() -> Path:
    """Return the daemon runtime directory."""
    return _runtime_dir() / "daemon"


def daemon_socket_path() -> Path:
    """Return the daemon socket path."""
    runtime = daemon_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        # Windows: use named pipe (no file path)
        return Path(r"\\.\pipe\vcc-daemon")
    else:
        # Unix: use socket file
        return runtime / "daemon.sock"


def daemon_pid_path() -> Path:
    """Return the daemon PID file path."""
    runtime = daemon_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / "daemon.pid"


def daemon_log_path() -> Path:
    """Return the daemon log file path."""
    runtime = daemon_runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / "daemon.log"


def connection_family() -> str:
    """Return the connection family for multiprocessing.connection."""
    if sys.platform == "win32":
        return "named-pipe"
    return "AF_UNIX"
