"""Tests for daemon path management."""

import sys
from pathlib import Path

import pytest

from vectorless_code.daemon_paths import (
    connection_family,
    daemon_log_path,
    daemon_pid_path,
    daemon_runtime_dir,
    daemon_socket_path,
)


def test_daemon_runtime_dir() -> None:
    """Test that runtime directory is created."""
    runtime_dir = daemon_runtime_dir()
    assert runtime_dir.is_dir()

    # Check it's in the right location
    if sys.platform == "darwin":
        assert "vectorless-code" in str(runtime_dir)
        assert "Library" in str(runtime_dir) or "Caches" in str(runtime_dir)
    elif sys.platform == "win32":
        assert "vectorless-code" in str(runtime_dir)
    else:
        assert "vectorless-code" in str(runtime_dir)
        assert ".cache" in str(runtime_dir) or "vectorless-code" in str(runtime_dir)


def test_daemon_socket_path() -> None:
    """Test socket path is valid."""
    sock_path = daemon_socket_path()
    if sys.platform == "win32":
        assert "\\\\.\\pipe\\" in str(sock_path)
    else:
        assert sock_path.parent.exists()
        assert "daemon.sock" == sock_path.name


def test_daemon_pid_path() -> None:
    """Test PID file path is valid."""
    pid_path = daemon_pid_path()
    assert pid_path.parent.exists()
    assert "daemon.pid" == pid_path.name


def test_daemon_log_path() -> None:
    """Test log file path is valid."""
    log_path = daemon_log_path()
    assert log_path.parent.exists()
    assert "daemon.log" == log_path.name


def test_connection_family() -> None:
    """Test connection family is valid."""
    family = connection_family()
    if sys.platform == "win32":
        assert family == "named-pipe"
    else:
        assert family == "AF_UNIX"
