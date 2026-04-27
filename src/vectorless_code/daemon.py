"""vcc daemon - background service for code indexing and search.

Simplified architecture using asyncio + Unix socket + JSON-RPC.
Replaces the complex msgspec + multiprocessing.Listener approach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vectorless_code._runtime import daemon_pid_path, daemon_socket_path
from vectorless_code._version import __version__
from vectorless_code.settings import load_user_settings

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------


@dataclass
class ProjectState:
    """State for a tracked project."""

    root: Path
    doc_id: str | None = None
    last_index_time: float = 0.0
    indexing: bool = False
    file_count: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    languages: dict[str, int] = field(default_factory=dict)

    @property
    def is_indexed(self) -> bool:
        """True if the project has been successfully indexed."""
        return self.doc_id is not None


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


class Daemon:
    """vcc daemon service using asyncio + Unix socket.

    Features:
    - JSON-RPC 2.0 over Unix socket (human-readable)
    - Project registry with doc_id caching
    - File watching for auto-recompile
    - Concurrent request handling
    - Graceful shutdown
    """

    def __init__(
        self,
        socket_path: str | None = None,
    ):
        """Initialize the daemon.

        Args:
            socket_path: Path to the Unix socket (default: from _runtime).
        """
        self._socket_path = socket_path or daemon_socket_path()

        # State
        self._projects: dict[str, ProjectState] = {}
        self._watchers: dict[str, FileWatcher] = {}
        self._index_locks: dict[str, asyncio.Lock] = {}
        self._reindex_tasks: dict[str, asyncio.Task[None]] = {}

        # Server
        self._server: asyncio.Server | None = None
        self._start_time: float = 0.0
        self._stop_requested = False

        # Settings (lazy loaded)
        self._user_settings: Any | None = None

    @property
    def is_running(self) -> bool:
        """True if the daemon server is running."""
        return self._server is not None

    @property
    def uptime_seconds(self) -> float:
        """Daemon uptime in seconds."""
        if self._start_time == 0.0:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def project_count(self) -> int:
        """Number of loaded projects."""
        return len(self._projects)

    def list_projects(self) -> list[dict[str, Any]]:
        """List all loaded projects with their status."""
        return [
            {
                "project_root": root,
                "indexed": state.is_indexed,
                "indexing": state.indexing,
                "file_count": state.file_count,
            }
            for root, state in self._projects.items()
        ]

    async def _load_user_settings(self) -> Any:
        """Load user settings (cached)."""
        if self._user_settings is None:
            self._user_settings = load_user_settings()
        return self._user_settings

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the daemon server."""
        if self._server is not None:
            logger.warning("Daemon already running")
            return

        # Ensure socket directory exists
        socket_path = Path(self._socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale socket if present
        if socket_path.exists():
            try:
                socket_path.unlink()
            except OSError:
                pass

        # Create Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(socket_path),
        )

        self._start_time = time.monotonic()
        logger.info("Daemon listening on %s", self._socket_path)

        # Set up signal handlers for graceful shutdown
        self._setup_signals()

    def _setup_signals(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_shutdown)
            except (RuntimeError, NotImplementedError):
                # Signals not supported on this platform
                pass

    def _request_shutdown(self) -> None:
        """Request a graceful shutdown."""
        logger.info("Shutdown requested")
        self._stop_requested = True

    async def run_until_shutdown(self) -> None:
        """Run the daemon until shutdown is requested."""
        if self._server is None:
            raise RuntimeError("Daemon not started")

        # Serve until stop is requested
        while not self._stop_requested:
            await asyncio.sleep(0.1)

        await self.stop()

    async def stop(self) -> None:
        """Stop the daemon and clean up resources."""
        logger.info("Stopping daemon...")

        # Stop all file watchers
        for watcher in list(self._watchers.values()):
            watcher.stop()
        self._watchers.clear()

        # Cancel reindex tasks
        for task in list(self._reindex_tasks.values()):
            task.cancel()
        self._reindex_tasks.clear()

        # Close server
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Remove socket file
        try:
            Path(self._socket_path).unlink(missing_ok=True)
        except OSError:
            pass

        logger.info("Daemon stopped")

    # ------------------------------------------------------------------
    # Client connection handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a client connection."""
        addr = writer.get_extra_info("peername")
        logger.debug("Client connected: %s", addr)

        try:
            # Read request line (JSON + newline)
            line = await reader.readline()
            if not line:
                logger.debug("Client disconnected (empty read): %s", addr)
                return

            try:
                request = json.loads(line.decode())
            except (json.JSONDecodeError, ValueError) as e:
                response = _error_response(
                    code=-32700,
                    message=f"Invalid JSON: {e}",
                    request_id=None,
                )
            else:
                # Dispatch request
                response = await self._dispatch(request)

            # Write response line (JSON + newline)
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()

        except Exception as e:
            logger.exception("Error handling client %s: %s", addr, e)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("Client disconnected: %s", addr)

    async def _dispatch(self, request: dict) -> dict:
        """Dispatch request to appropriate handler."""
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")

        try:
            if method == "compile":
                result = await self._handle_compile(params)
            elif method == "ask":
                result = await self._handle_ask(params)
            elif method == "status":
                result = await self._handle_status(params)
            elif method == "stop":
                result = await self._handle_stop()
            elif method == "daemon_status":
                result = {
                    "version": str(__version__),
                    "uptime_seconds": self.uptime_seconds,
                    "projects": self.list_projects(),
                }
            elif method == "ping":
                result = {"pong": True, "uptime": self.uptime_seconds}
            else:
                return _error_response(
                    code=-32601,
                    message=f"Unknown method: {method}",
                    request_id=req_id,
                )

            return _success_response(result, req_id)

        except Exception as e:
            logger.exception("Error handling %s request: %s", method, e)
            return _error_response(
                code=-32603,
                message=str(e),
                request_id=req_id,
            )

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    async def _handle_compile(self, params: dict) -> dict:
        """Handle compile request."""
        from vectorless_code.compile import compile_project

        project_root = params.get("project_root")
        if not project_root:
            raise ValueError("Missing project_root parameter")

        # Get or create project state
        if project_root not in self._projects:
            self._projects[project_root] = ProjectState(root=Path(project_root))

        project = self._projects[project_root]

        # Get index lock (one index at a time per project)
        lock = self._index_locks.setdefault(project_root, asyncio.Lock())

        async with lock:
            project.indexing = True

            try:
                # Compile project
                result = await compile_project(
                    project_root=Path(project_root),
                    user_settings=await self._load_user_settings(),
                )

                if not result.ok:
                    raise RuntimeError(result.error or "Compilation failed")

                # Update state
                project.doc_id = result.doc_id
                project.last_index_time = time.monotonic()
                project.file_count = result.file_count
                project.total_lines = result.total_lines
                project.total_bytes = result.total_bytes
                project.languages = result.languages

                # Start file watcher if not running
                if project_root not in self._watchers:
                    watcher = FileWatcher(
                        path=Path(project_root),
                        on_change=lambda: self._schedule_reindex(project_root),
                    )
                    watcher.start()
                    self._watchers[project_root] = watcher

                return {
                    "success": True,
                    "doc_id": project.doc_id,
                    "file_count": project.file_count,
                    "total_lines": project.total_lines,
                    "total_bytes": project.total_bytes,
                    "languages": project.languages,
                }

            finally:
                project.indexing = False

    async def _handle_ask(self, params: dict) -> dict:
        """Handle ask request."""
        from vectorless_code.ask import ask_codebase

        project_root = params.get("project_root")
        query = params.get("query")
        limit = params.get("limit", 10)
        offset = params.get("offset", 0)

        if not project_root:
            raise ValueError("Missing project_root parameter")
        if not query:
            raise ValueError("Missing query parameter")

        # Check if project exists
        if project_root not in self._projects:
            raise ValueError(f"Project not loaded: {project_root}")

        project = self._projects[project_root]

        # Check if project is indexed
        if not project.doc_id:
            raise ValueError(f"Project not indexed: {project_root}. Call compile() first.")

        # Wait for any ongoing indexing to complete
        lock = self._index_locks.get(project_root)
        if lock and lock.locked():
            async with lock:
                pass

        # Search
        output = await ask_codebase(
            question=query,
            doc_ids=[project.doc_id],
            user_settings=await self._load_user_settings(),
        )

        results = []
        for ev in output.evidence:
            results.append(
                {
                    "file_path": ev.source_path or "",
                    "node_title": ev.node_title,
                    "content": ev.content,
                    "doc_name": ev.doc_name,
                }
            )

        # Apply pagination
        paginated = results[offset : offset + limit]

        return {
            "success": True,
            "results": paginated,
            "total_returned": len(paginated),
            "offset": offset,
            "confidence": output.confidence,
        }

    async def _handle_status(self, params: dict) -> dict:
        """Handle status request."""
        project_root = params.get("project_root")
        if not project_root:
            raise ValueError("Missing project_root parameter")

        if project_root not in self._projects:
            # Return empty status for unknown projects
            return {
                "indexed": False,
                "indexing": False,
                "file_count": 0,
                "total_lines": 0,
                "total_bytes": 0,
                "languages": {},
                "doc_id": None,
            }

        project = self._projects[project_root]
        return {
            "indexed": project.is_indexed,
            "indexing": project.indexing,
            "file_count": project.file_count,
            "total_lines": project.total_lines,
            "total_bytes": project.total_bytes,
            "languages": project.languages,
            "doc_id": project.doc_id,
        }

    async def _handle_stop(self) -> dict:
        """Handle stop request."""
        self._request_shutdown()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Auto-reindex scheduling
    # ------------------------------------------------------------------

    def _schedule_reindex(self, project_root: str) -> None:
        """Schedule a reindex for the project (called from FileWatcher)."""
        # Cancel existing task if any
        if project_root in self._reindex_tasks:
            self._reindex_tasks[project_root].cancel()

        # Create new reindex task
        async def _do_reindex() -> None:
            # Wait for debounce period
            await asyncio.sleep(1.0)

            try:
                logger.info("Auto-reindexing %s", project_root)
                await self._handle_compile({"project_root": project_root})
                logger.info("Auto-reindex complete for %s", project_root)
            except Exception as e:
                logger.error("Auto-reindex failed for %s: %s", project_root, e)

        task = asyncio.create_task(_do_reindex())
        self._reindex_tasks[project_root] = task


# ---------------------------------------------------------------------------
# File watcher (inline, since it's tightly coupled with daemon)
# ---------------------------------------------------------------------------


class FileWatcher:
    """Monitor a directory for file changes using watchdog.

    Debounces rapid changes to avoid excessive recompilations.
    """

    # Source file extensions to monitor
    SOURCE_EXTENSIONS = {
        # Python
        ".py",
        ".pyi",
        # JavaScript/TypeScript
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        # Rust
        ".rs",
        # Go
        ".go",
        # Java
        ".java",
        # C/C++
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cxx",
        # Ruby
        ".rb",
        # Kotlin
        ".kt",
        ".kts",
        # Scala
        ".scala",
        # Others
        ".php",
        ".sh",
        ".bash",
        ".lua",
        ".sql",
    }

    def __init__(
        self,
        path: Path,
        on_change: callable,
        debounce_secs: float = 0.5,
    ):
        """Initialize the file watcher.

        Args:
            path: Directory to monitor (will be watched recursively).
            on_change: Callback to invoke when source files change.
            debounce_secs: Seconds to wait after last change before triggering.
        """
        self._path = path
        self._on_change = on_change
        self._debounce_secs = debounce_secs

        self._observer = None
        self._handler: _ChangeHandler | None = None

    @property
    def is_running(self) -> bool:
        """True if the watcher is currently running."""
        return self._observer is not None

    def start(self) -> None:
        """Start watching the directory."""
        if self._observer is not None:
            logger.warning("FileWatcher already running for %s", self._path)
            return

        try:
            from watchdog.observers import Observer
        except ImportError:
            logger.error(
                "watchdog not installed. File watching disabled. "
                "Install with: pip install watchdog"
            )
            return

        self._handler = _ChangeHandler(self._on_change, self._debounce_secs)
        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self._path),
            recursive=True,
        )
        self._observer.start()
        logger.info("FileWatcher started for %s", self._path)

    def stop(self) -> None:
        """Stop watching and clean up resources."""
        if self._observer is None:
            return

        if self._handler is not None:
            self._handler.stop()
            self._handler = None

        self._observer.stop()
        self._observer.join(timeout=5.0)
        self._observer = None
        logger.info("FileWatcher stopped for %s", self._path)


class _ChangeHandler:
    """Internal watchdog event handler with debouncing."""

    def __init__(self, callback: callable, debounce_secs: float = 0.5):
        self._callback = callback
        self._debounce_secs = debounce_secs
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_modified(self, event) -> None:
        """Handle file modified event."""
        if event.is_directory:
            return

        path = event.src_path

        # Filter to source files only
        if not any(path.endswith(ext) for ext in FileWatcher.SOURCE_EXTENSIONS):
            return

        # Skip hidden files and cache directories
        if "/." in path or "\\." in path:
            return
        if "/node_modules/" in path or "\\node_modules\\" in path:
            return
        if "/.vectorless_code/" in path or "\\.vectorless_code\\" in path:
            return
        if "/__pycache__/" in path or "\\__pycache__\\" in path:
            return
        if "/.git/" in path or "\\.git\\" in path:
            return

        logger.debug("File changed: %s", path)

        # Debounce: cancel existing timer and start new one
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(self._debounce_secs, self._callback)
            self._timer.start()

    def stop(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _success_response(result: Any, request_id: int | None) -> dict:
    """Create a JSON-RPC success response."""
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "result": result,
    }
    if request_id is not None:
        response["id"] = request_id
    return response


def _error_response(code: int, message: str, request_id: int | None) -> dict:
    """Create a JSON-RPC error response."""
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    }
    if request_id is not None:
        response["id"] = request_id
    return response


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------


def _write_pid_file() -> None:
    """Write current PID to the pid file."""
    pid_path = daemon_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    pid_path.write_text(str(os.getpid()))
    logger.debug("Wrote PID file: %s", pid_path)


def _remove_pid_file() -> None:
    """Remove the PID file."""
    pid_path = daemon_pid_path()
    try:
        stored = pid_path.read_text().strip()
        if stored == str(os.getpid()):
            pid_path.unlink(missing_ok=True)
            logger.debug("Removed PID file: %s", pid_path)
    except (FileNotFoundError, ValueError, OSError):
        pass


def run_daemon() -> None:
    """Main entry point for running the daemon (blocking)."""
    logger.info("vectorless-code daemon v%s starting (PID %d)", __version__, os.getpid())

    # Write PID file
    _write_pid_file()

    # Create and run daemon
    daemon = Daemon()

    async def _run() -> None:
        try:
            await daemon.start()
            await daemon.run_until_shutdown()
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.exception("Daemon error: %s", e)
            raise
        finally:
            await daemon.stop()
            _remove_pid_file()

    try:
        asyncio.run(_run())
    finally:
        # Ensure PID file is cleaned up
        _remove_pid_file()

    logger.info("Daemon exited")

    # Use os._exit to ensure all threads/child processes are terminated
    if threading.current_thread() is threading.main_thread():
        os._exit(0)


# For direct execution
if __name__ == "__main__":
    run_daemon()
