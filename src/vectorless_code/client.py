"""Client for communicating with the daemon.

Per-request connection model: each function opens a fresh connection,
performs the version handshake, sends one request, reads the response(s),
and closes. There is no persistent connection object.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from multiprocessing.connection import Client, Connection
from pathlib import Path

from ._daemon_paths import (
    connection_family,
    daemon_log_path,
    daemon_pid_path,
    daemon_socket_path,
)
from ._version import __version__
from .protocol import (
    DaemonEnvRequest,
    DaemonEnvResponse,
    DaemonStatusResponse,
    DoctorCheckResult,
    DoctorRequest,
    DoctorResponse,
    ErrorResponse,
    HandshakeRequest,
    HandshakeResponse,
    IndexingProgress,
    IndexProgressUpdate,
    IndexRequest,
    IndexResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    ProjectStatusResponse,
    RemoveProjectRequest,
    RemoveProjectResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    StopRequest,
    StopResponse,
    decode_response,
    encode_request,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-request connection helpers
# ---------------------------------------------------------------------------


_daemon_ensured = False

_surfaced_warnings: set[str] = set()


def print_warning(message: str) -> None:
    """Render a user-facing warning to stderr."""
    import click

    click.secho(f"Warning: {message}", fg="yellow", err=True)


def _print_handshake_warnings(resp: HandshakeResponse) -> None:
    """Print any new daemon-side warnings to stderr (once per process)."""
    for w in resp.warnings:
        if w in _surfaced_warnings:
            continue
        _surfaced_warnings.add(w)
        print_warning(w)


def _is_daemon_supervised() -> bool:
    """True when an external supervisor owns daemon respawn."""
    return os.environ.get("VECTORLESS_DAEMON_SUPERVISED") == "1"


def _connect_and_handshake() -> Connection:
    """Connect to the daemon and perform the version handshake."""
    global _daemon_ensured

    if _daemon_ensured:
        return _raw_connect_and_handshake()

    try:
        conn = _raw_connect_and_handshake()
        _daemon_ensured = True
        return conn
    except DaemonVersionError:
        stop_daemon()
    except (ConnectionRefusedError, OSError):
        pass

    if _is_daemon_supervised():
        _wait_for_daemon()
    else:
        proc = start_daemon()
        _wait_for_daemon(proc=proc)

    for _attempt in range(10):
        try:
            conn = _raw_connect_and_handshake()
            _daemon_ensured = True
            return conn
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)

    raise RuntimeError("Failed to connect to daemon after starting it")


def _raw_connect_and_handshake() -> Connection:
    """Low-level connect + handshake without auto-start logic."""
    sock = daemon_socket_path()
    if sys.platform != "win32" and not os.path.exists(sock):
        raise ConnectionRefusedError(f"Daemon socket not found: {sock}")
    try:
        conn = Client(str(sock), family=connection_family())
    except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
        raise ConnectionRefusedError(f"Cannot connect to daemon: {e}") from e

    try:
        conn.send_bytes(encode_request(HandshakeRequest(version=__version__)))
        data = conn.recv_bytes()
    except (EOFError, OSError) as e:
        conn.close()
        raise ConnectionRefusedError(f"Handshake failed: {e}") from e

    resp = decode_response(data)
    if isinstance(resp, ErrorResponse):
        conn.close()
        raise RuntimeError(f"Daemon error: {resp.message}")
    if not isinstance(resp, HandshakeResponse):
        conn.close()
        raise RuntimeError(f"Unexpected handshake response: {type(resp).__name__}")
    if not resp.ok or _needs_restart(resp):
        conn.close()
        raise DaemonVersionError(resp)
    _print_handshake_warnings(resp)
    return conn


class DaemonVersionError(RuntimeError):
    """Raised when the daemon has a version or settings mismatch."""

    def __init__(self, resp: HandshakeResponse) -> None:
        self.resp = resp
        super().__init__(
            f"Daemon version mismatch (daemon={resp.daemon_version}, "
            f"client={__version__}). Please retry — the daemon may need a restart."
        )


class DaemonStartError(RuntimeError):
    """Raised when the daemon process fails to start."""

    def __init__(self, message: str, log: str | None = None) -> None:
        self.log = log
        super().__init__(message)


def _read_daemon_log() -> str | None:
    """Read the daemon log file, returning its content or None."""
    log_path = daemon_log_path()
    try:
        content = log_path.read_text().strip()
        return content if content else None
    except (FileNotFoundError, OSError):
        return None


def _send(req: Request) -> Response:
    """Open connection, handshake, send one request, read one response, close."""
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(req))
        data = conn.recv_bytes()
    except (EOFError, OSError) as e:
        raise RuntimeError(f"Connection to daemon lost: {e}") from e
    finally:
        conn.close()
    resp = decode_response(data)
    if isinstance(resp, ErrorResponse):
        raise RuntimeError(f"Daemon error: {resp.message}")
    return resp


# ---------------------------------------------------------------------------
# Public API — one function per request type
# ---------------------------------------------------------------------------


def index(
    project_root: str,
    on_progress: Callable[[IndexingProgress], None] | None = None,
    on_waiting: Callable[[], None] | None = None,
) -> IndexResponse:
    """Request indexing with streaming progress. Blocks until complete."""
    from .settings import normalize_path

    project_root = normalize_path(project_root)
    logger.debug("Requesting index for %s", project_root)
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(IndexRequest(project_root=project_root)))
        while True:
            try:
                data = conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during indexing")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                raise RuntimeError(f"Daemon error: {resp.message}")
            if isinstance(resp, IndexWaitingNotice):
                if on_waiting is not None:
                    on_waiting()
                continue
            if isinstance(resp, IndexProgressUpdate):
                if on_progress is not None:
                    on_progress(resp.progress)
                continue
            if isinstance(resp, IndexResponse):
                return resp
            raise RuntimeError(f"Unexpected response: {type(resp).__name__}")
    finally:
        conn.close()


def search(
    project_root: str,
    query: str,
    doc_ids: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
    on_waiting: Callable[[], None] | None = None,
) -> SearchResponse:
    """Search the codebase."""
    from .settings import normalize_path

    project_root = normalize_path(project_root)
    logger.debug("Searching %s: %s", project_root, query[:50])
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(
            encode_request(
                SearchRequest(
                    project_root=project_root,
                    query=query,
                    doc_ids=doc_ids,
                    limit=limit,
                    offset=offset,
                )
            )
        )
        while True:
            try:
                data = conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during search")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                raise RuntimeError(f"Daemon error: {resp.message}")
            if isinstance(resp, IndexWaitingNotice):
                if on_waiting is not None:
                    on_waiting()
                continue
            if isinstance(resp, SearchResponse):
                return resp
            raise RuntimeError(f"Unexpected response: {type(resp).__name__}")
    finally:
        conn.close()


def project_status(project_root: str) -> ProjectStatusResponse:
    from .settings import normalize_path

    return _send(ProjectStatusRequest(project_root=normalize_path(project_root)))  # type: ignore[return-value]


def daemon_status() -> DaemonStatusResponse:
    return _send(DaemonStatusRequest())  # type: ignore[return-value]


def remove_project(project_root: str) -> RemoveProjectResponse:
    from .settings import normalize_path

    return _send(RemoveProjectRequest(project_root=normalize_path(project_root)))  # type: ignore[return-value]


def stop() -> StopResponse:
    return _send(StopRequest())  # type: ignore[return-value]


def daemon_env() -> DaemonEnvResponse:
    """Get environment variable names from the daemon."""
    return _send(DaemonEnvRequest())  # type: ignore[return-value]


def doctor(
    project_root: str | None = None,
    on_result: Callable[[DoctorCheckResult], None] | None = None,
) -> list[DoctorCheckResult]:
    """Run doctor checks via daemon, streaming results to on_result callback."""
    from .settings import normalize_path

    if project_root is not None:
        project_root = normalize_path(project_root)
    conn = _connect_and_handshake()
    try:
        conn.send_bytes(encode_request(DoctorRequest(project_root=project_root)))
        results: list[DoctorCheckResult] = []
        while True:
            try:
                data = conn.recv_bytes()
            except EOFError:
                raise RuntimeError("Connection to daemon lost during doctor checks")
            resp = decode_response(data)
            if isinstance(resp, ErrorResponse):
                raise RuntimeError(f"Daemon error: {resp.message}")
            if isinstance(resp, DoctorResponse):
                results.append(resp.result)
                if on_result is not None:
                    on_result(resp.result)
                if resp.final:
                    break
            else:
                raise RuntimeError(f"Unexpected response: {type(resp).__name__}")
        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Daemon lifecycle helpers
# ---------------------------------------------------------------------------


def is_daemon_running() -> bool:
    """Check if the daemon is running."""
    if sys.platform == "win32":
        try:
            conn = Client(str(daemon_socket_path()), family=connection_family())
            conn.close()
            return True
        except (ConnectionRefusedError, OSError):
            return False
    return os.path.exists(daemon_socket_path())


def start_daemon() -> subprocess.Popen[bytes]:
    """Start the daemon as a background process."""
    daemon_runtime_dir().mkdir(parents=True, exist_ok=True)
    log_path = daemon_log_path()

    vcc_path = _find_vcc_executable()
    if vcc_path:
        cmd = [vcc_path, "run-daemon"]
    else:
        cmd = [sys.executable, "-m", "vectorless_code.cli", "run-daemon"]

    log_fd = open(log_path, "w")
    if sys.platform == "win32":
        _create_no_window = 0x08000000
        proc = subprocess.Popen(
            cmd,
            stdout=log_fd,
            stderr=log_fd,
            stdin=subprocess.DEVNULL,
            creationflags=_create_no_window,
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


def _find_vcc_executable() -> str | None:
    """Find the vcc executable in PATH or the same directory as python."""
    python_dir = Path(sys.executable).parent
    names = ["vcc.exe", "vcc"] if sys.platform == "win32" else ["vcc"]
    for name in names:
        vcc = python_dir / name
        if vcc.exists():
            return str(vcc)
    return None


def _pid_alive(pid: int) -> bool:
    """Return True if *pid* is still running."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = getattr(ctypes, "windll").kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_for_daemon_exit(timeout: float) -> bool:
    """Wait up to *timeout* seconds for the daemon to finish cleanup."""
    pid_path = daemon_pid_path()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_path.exists():
            return True
        time.sleep(0.1)
    return not pid_path.exists()


def stop_daemon() -> None:
    """Stop the daemon gracefully."""
    global _daemon_ensured
    _daemon_ensured = False
    _surfaced_warnings.clear()
    pid_path = daemon_pid_path()

    pid: int | None = None
    try:
        pid = int(pid_path.read_text().strip())
        if pid == os.getpid():
            pid = None
    except (FileNotFoundError, ValueError):
        pass

    try:
        conn = _raw_connect_and_handshake()
        try:
            conn.send_bytes(encode_request(StopRequest()))
            conn.recv_bytes()
        finally:
            conn.close()
    except (ConnectionRefusedError, OSError, RuntimeError, DaemonVersionError):
        pass

    if _wait_for_daemon_exit(timeout=3.0):
        return

    if pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        if _wait_for_daemon_exit(timeout=2.0):
            return

    if sys.platform != "win32" and pid is not None and _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    _cleanup_stale_files(pid_path, pid)


def _cleanup_stale_files(pid_path: Path, pid: int | None) -> None:
    """Remove socket and PID file after the daemon has exited."""
    if sys.platform != "win32":
        sock = daemon_socket_path()
        try:
            Path(sock).unlink(missing_ok=True)
        except Exception:
            pass
    if pid is not None:
        try:
            stored = pid_path.read_text().strip()
            if stored == str(pid):
                pid_path.unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
    else:
        try:
            pid_path.unlink(missing_ok=True)
        except Exception:
            pass


def _wait_for_daemon(
    timeout: float = 30.0,
    proc: subprocess.Popen[bytes] | None = None,
) -> None:
    """Wait for the daemon socket/pipe to become available."""
    deadline = time.monotonic() + timeout
    sock_path = daemon_socket_path()
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            try:
                conn = Client(str(sock_path), family=connection_family())
                conn.close()
                return
            except (ConnectionRefusedError, OSError):
                pass
        else:
            if os.path.exists(sock_path):
                return

        if proc is not None and proc.poll() is not None:
            log = _read_daemon_log()
            msg = "Daemon process exited before it became ready."
            if log:
                msg += f"\n\nDaemon log:\n{log}"
            raise DaemonStartError(msg, log=log)

        time.sleep(0.2)

    log = _read_daemon_log()
    msg = "Daemon did not start in time."
    if log:
        msg += f"\n\nDaemon log:\n{log}"
    raise DaemonStartError(msg, log=log)


def _needs_restart(resp: HandshakeResponse) -> bool:
    """Check if the daemon needs to be restarted."""
    if not resp.ok:
        return True
    from .settings import global_settings_mtime_us

    current_mtime = global_settings_mtime_us()
    if current_mtime is not None and current_mtime != resp.global_settings_mtime_us:
        return True
    return False


async def _bg_index(project_root: str) -> None:
    """Index in background. Each call opens its own daemon connection."""
    import asyncio

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, lambda: index(project_root))
    except Exception:
        pass
