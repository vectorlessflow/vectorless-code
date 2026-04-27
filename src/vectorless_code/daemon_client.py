"""Simplified client for communicating with the daemon.

Uses JSON-RPC over Unix socket instead of msgpack + multiprocessing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, TypeVar

from vectorless_code.daemon.protocol import (
    METHOD_ASK,
    METHOD_COMPILE,
    METHOD_PING,
    METHOD_STATUS,
    METHOD_STOP,
    JSONRPCRequest,
    JSONRPCResponse,
)
from vectorless_code.daemon_paths import daemon_socket_path

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DaemonError(RuntimeError):
    """Base exception for daemon errors."""

    pass


class DaemonStartError(DaemonError):
    """Raised when the daemon fails to start."""

    def __init__(self, message: str, log: str | None = None):
        super().__init__(message)
        self.log = log


class DaemonVersionError(DaemonError):
    """Raised when the daemon has a version mismatch."""

    pass


class RPCError(DaemonError):
    """Raised when the daemon returns an error response."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DaemonClient:
    """Client for communicating with the daemon over Unix socket.

    Example:
        ```python
        client = DaemonClient()

        # Index a project
        result = await client.index("/path/to/project")
        print(f"Indexed {result['file_count']} files")

        # Search
        result = await client.search("/path/to/project", "authentication logic")
        for r in result['results']:
            print(f"{r['file_path']}: {r['node_title']}")
        ```
    """

    def __init__(self, socket_path: Path | None = None):
        """Initialize the client.

        Args:
            socket_path: Path to the daemon Unix socket.
        """
        self._socket_path = socket_path or daemon_socket_path()
        self._request_id = 0
        self._daemon_checked = False

    @property
    def socket_path(self) -> Path:
        """Get the socket path."""
        return self._socket_path

    def _next_id(self) -> int:
        """Get the next request ID."""
        self._next_id.counter += 1  # type: ignore
        return self._next_id.counter  # type: ignore

    _next_id.counter = 0

    # ------------------------------------------------------------------
    # Low-level RPC
    # ------------------------------------------------------------------

    async def _call(
        self,
        method: str,
        params: dict,
        timeout: float = 120.0,
    ) -> Any:
        """Send a JSON-RPC request and return the result.

        Args:
            method: RPC method name.
            params: Method parameters.
            timeout: Request timeout in seconds.

        Returns:
            The result field from the response.

        Raises:
            RPCError: If the daemon returns an error.
            DaemonError: For communication errors.
        """
        # Ensure daemon is running
        await self._ensure_daemon()

        request = JSONRPCRequest(
            method=method,
            params=params,
            id=self._next_id(),
        )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=5.0,
            )

        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise DaemonError(f"Cannot connect to daemon at {self._socket_path}: {e}") from e

        try:
            # Send request
            writer.write(request.to_json().encode() + b"\n")
            await writer.drain()

            # Read response
            response_line = await asyncio.wait_for(
                reader.readline(),
                timeout=timeout,
            )

            if not response_line:
                raise DaemonError("Connection closed by daemon")

            response = JSONRPCResponse.from_json(response_line.decode())

            # Check for error
            if response.error:
                raise RPCError(
                    code=response.error.code,
                    message=response.error.message,
                    data=response.error.data,
                )

            return response.result

        except TimeoutError:
            raise DaemonError(f"Request timeout after {timeout}s") from None
        except (json.JSONDecodeError, ValueError) as e:
            raise DaemonError(f"Invalid response from daemon: {e}") from e
        finally:
            writer.close()
            await writer.wait_closed()

    # ------------------------------------------------------------------
    # Daemon lifecycle
    # ------------------------------------------------------------------

    async def _ensure_daemon(self) -> None:
        """Ensure the daemon is running, starting it if necessary."""
        if self._daemon_checked:
            return

        # Check if already running
        if await self._ping():
            self._daemon_checked = True
            return

        # Check if supervised (Docker, etc.)
        if self._is_supervised():
            # Wait for supervised daemon to become ready
            logger.info("Waiting for supervised daemon...")
            for _ in range(50):  # 5 seconds
                await asyncio.sleep(0.1)
                if await self._ping():
                    self._daemon_checked = True
                    return
            raise DaemonError("Supervised daemon did not start in time")

        # Start the daemon
        logger.info("Starting daemon...")
        proc = self._start_daemon_process()

        # Wait for daemon to become ready
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log = self._read_daemon_log()
                msg = "Daemon process exited before it became ready."
                if log:
                    msg += f"\n\nDaemon log:\n{log}"
                raise DaemonStartError(msg, log=log)

            await asyncio.sleep(0.2)
            if await self._ping():
                self._daemon_checked = True
                return

        raise DaemonStartError("Daemon did not start in time")

    async def _ping(self) -> bool:
        """Check if the daemon is alive."""
        try:
            result = await self._call(METHOD_PING, {}, timeout=1.0)
            return isinstance(result, dict) and result.get("pong") is True
        except Exception:
            return False

    def _is_supervised(self) -> bool:
        """Check if running in supervised mode (e.g., Docker)."""
        return os.environ.get("VECTORLESS_DAEMON_SUPERVISED") == "1"

    def _start_daemon_process(self) -> subprocess.Popen:
        """Start the daemon as a background process."""
        from vectorless_code.daemon_paths import daemon_log_path, daemon_runtime_dir

        runtime_dir = daemon_runtime_dir()
        runtime_dir.mkdir(parents=True, exist_ok=True)

        log_path = daemon_log_path()

        # Find the vcc executable or use python -m
        vcc_path = self._find_vcc_executable()
        if vcc_path:
            cmd = [vcc_path, "run-daemon"]
        else:
            cmd = [sys.executable, "-m", "vectorless_code.cli", "run-daemon"]

        log_fd = open(log_path, "w")

        if sys.platform == "win32":
            create_no_window = 0x08000000
            proc = subprocess.Popen(
                cmd,
                stdout=log_fd,
                stderr=log_fd,
                stdin=subprocess.DEVNULL,
                creationflags=create_no_window,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=log_fd,
                stderr=log_fd,
                stdin=subprocess.DEVNULL,
            )

        log_fd.close()
        return proc

    def _find_vcc_executable(self) -> str | None:
        """Find the vcc executable."""
        python_dir = Path(sys.executable).parent
        names = ["vcc.exe", "vcc"] if sys.platform == "win32" else ["vcc"]
        for name in names:
            vcc = python_dir / name
            if vcc.exists():
                return str(vcc)
        return None

    def _read_daemon_log(self) -> str | None:
        """Read the daemon log file."""
        from vectorless_code.daemon_paths import daemon_log_path

        log_path = daemon_log_path()
        try:
            content = log_path.read_text().strip()
            return content if content else None
        except (FileNotFoundError, OSError):
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def index(
        self,
        project_root: str,
        timeout: float = 300.0,
    ) -> dict:
        """Index a project.

        Args:
            project_root: Path to the project root.
            timeout: Request timeout in seconds.

        Returns:
            Dict with keys: success, doc_id, file_count, total_lines, etc.
        """
        return await self._call(
            METHOD_COMPILE,
            {"project_root": project_root},
            timeout=timeout,
        )

    async def search(
        self,
        project_root: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        timeout: float = 120.0,
    ) -> dict:
        """Search a project.

        Args:
            project_root: Path to the project root.
            query: Search query.
            limit: Maximum number of results.
            offset: Number of results to skip.
            timeout: Request timeout in seconds.

        Returns:
            Dict with keys: success, results, confidence, etc.
        """
        return await self._call(
            METHOD_ASK,
            {
                "project_root": project_root,
                "query": query,
                "limit": limit,
                "offset": offset,
            },
            timeout=timeout,
        )

    async def status(
        self,
        project_root: str,
        timeout: float = 10.0,
    ) -> dict:
        """Get project status.

        Args:
            project_root: Path to the project root.
            timeout: Request timeout in seconds.

        Returns:
            Dict with keys: indexed, indexing, file_count, etc.
        """
        return await self._call(
            METHOD_STATUS,
            {"project_root": project_root},
            timeout=timeout,
        )

    async def stop(self, timeout: float = 5.0) -> dict:
        """Stop the daemon.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            Dict with key: ok
        """
        result = await self._call(METHOD_STOP, {}, timeout=timeout)
        self._daemon_checked = False
        return result

    async def daemon_status(self, timeout: float = 5.0) -> dict:
        """Get daemon status.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            Dict with keys: version, uptime_seconds, projects
        """
        return await self._call("daemon_status", {}, timeout=timeout)

    async def project_status(
        self,
        project_root: str,
        timeout: float = 10.0,
    ) -> dict:
        """Get project status (alias for status)."""
        return await self.status(project_root, timeout=timeout)


# ---------------------------------------------------------------------------
# Convenience wrappers (sync API)
# ---------------------------------------------------------------------------


def _run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def index(project_root: str) -> dict:
    """Index a project (synchronous wrapper)."""
    client = DaemonClient()
    return _run_async(client.index(project_root))


def search(project_root: str, query: str, limit: int = 10) -> dict:
    """Search a project (synchronous wrapper)."""
    client = DaemonClient()
    return _run_async(client.search(project_root, query, limit))


def status(project_root: str) -> dict:
    """Get project status (synchronous wrapper)."""
    client = DaemonClient()
    return _run_async(client.status(project_root))


def stop() -> dict:
    """Stop the daemon (synchronous wrapper)."""
    client = DaemonClient()
    return _run_async(client.stop())


# ---------------------------------------------------------------------------
# Daemon lifecycle functions (for CLI)
# ---------------------------------------------------------------------------


def is_daemon_running() -> bool:
    """Check if the daemon is running by checking the PID file."""
    from vectorless_code.daemon_paths import daemon_pid_path

    pid_path = daemon_pid_path()
    if not pid_path.exists():
        return False

    try:
        pid_str = pid_path.read_text().strip()
        pid = int(pid_str)

        # Check if process is running
        if sys.platform == "win32":
            import psutil

            return psutil.pid_exists(pid)
        else:
            try:
                os.kill(pid, 0)  # Send null signal
                return True
            except OSError:
                return False
    except (ValueError, OSError, ProcessLookupError):
        return False


def start_daemon() -> subprocess.Popen:
    """Start the daemon as a background process.

    Returns:
        The subprocess.Popen object for the daemon process.
    """
    from vectorless_code.daemon_paths import daemon_log_path, daemon_runtime_dir

    runtime_dir = daemon_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    log_path = daemon_log_path()

    # Find the vcc executable or use python -m
    vcc_path = _find_vcc_executable_static()
    if vcc_path:
        cmd = [vcc_path, "run-daemon"]
    else:
        cmd = [sys.executable, "-m", "vectorless_code.cli", "run-daemon"]

    log_fd = open(log_path, "w")

    if sys.platform == "win32":
        create_no_window = 0x08000000
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            creationflags=create_no_window,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
        )

    log_fd.close()
    logger.info("Started daemon process with PID %d", proc.pid)
    return proc


def stop_daemon() -> None:
    """Stop the daemon by sending SIGTERM."""
    from vectorless_code.daemon_paths import daemon_pid_path

    pid_path = daemon_pid_path()
    if not pid_path.exists():
        logger.warning("Daemon PID file not found")
        return

    try:
        pid_str = pid_path.read_text().strip()
        pid = int(pid_str)

        if sys.platform == "win32":
            import psutil

            proc = psutil.Process(pid)
            proc.terminate()
        else:
            os.kill(pid, signal.SIGTERM)

        logger.info("Sent SIGTERM to daemon PID %d", pid)
    except (ValueError, OSError, ProcessLookupError) as e:
        logger.warning("Failed to stop daemon: %s", e)


def _find_vcc_executable_static() -> str | None:
    """Find the vcc executable (static version for module-level functions)."""
    python_dir = Path(sys.executable).parent
    names = ["vcc.exe", "vcc"] if sys.platform == "win32" else ["vcc"]
    for name in names:
        vcc = python_dir / name
        if vcc.exists():
            return str(vcc)
    return None


def _wait_for_daemon(proc: subprocess.Popen | None = None, timeout: float = 30.0) -> None:
    """Wait for the daemon to become ready.

    Args:
        proc: The subprocess.Popen object (optional, for early exit detection).
        timeout: Maximum time to wait in seconds.
    """
    start = time.monotonic()
    client = DaemonClient()

    while time.monotonic() - start < timeout:
        if proc and proc.poll() is not None:
            raise RuntimeError("Daemon process exited before becoming ready")

        try:
            result = asyncio.run(client._call(METHOD_PING, {}, timeout=1.0))
            if result.get("pong"):
                logger.info("Daemon is ready")
                return
        except Exception:
            pass

        time.sleep(0.2)

    raise RuntimeError(f"Daemon did not become ready within {timeout}s")
