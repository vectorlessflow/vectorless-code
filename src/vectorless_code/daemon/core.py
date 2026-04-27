"""Core daemon implementation with asyncio and Unix socket.

Replaces the multiprocessing.Listener-based daemon with a simpler
asyncio-based implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vectorless_code._version import __version__
from vectorless_code.daemon.protocol import (
    METHOD_ASK,
    METHOD_COMPILE,
    METHOD_PING,
    METHOD_STATUS,
    METHOD_STOP,
    Error,
    JSONRPCRequest,
    JSONRPCResponse,
)
from vectorless_code.daemon.watcher import FileWatcher
from vectorless_code.settings import load_user_settings

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
    """Main daemon process using asyncio + Unix socket.

    Features:
    - JSON-RPC 2.0 protocol over Unix socket
    - Project registry with doc_id caching
    - File watching for auto-recompile
    - Concurrent request handling
    - Graceful shutdown
    """

    def __init__(
        self,
        socket_path: Path | None = None,
        user_settings_path: Path | None = None,
    ):
        """Initialize the daemon.

        Args:
            socket_path: Path to the Unix socket file.
            user_settings_path: Path to user settings file.
        """
        from vectorless_code.daemon_paths import daemon_socket_path

        self._socket_path = socket_path or daemon_socket_path()
        self._user_settings_path = user_settings_path or user_settings_path()

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
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove stale socket if present
        if self._socket_path.exists():
            try:
                self._socket_path.unlink()
            except OSError:
                pass

        # Create Unix socket server
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
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
            self._socket_path.unlink(missing_ok=True)
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
                request = JSONRPCRequest.from_json(line.decode())
            except (json.JSONDecodeError, ValueError) as e:
                response = JSONRPCResponse.from_error(
                    code=Error.PARSE_ERROR,
                    message=f"Invalid JSON: {e}",
                )
            else:
                # Dispatch request
                response = await self._dispatch(request)

            # Write response line (JSON + newline)
            writer.write(response.to_json().encode() + b"\n")
            await writer.drain()

        except Exception as e:
            logger.exception("Error handling client %s: %s", addr, e)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("Client disconnected: %s", addr)

    async def _dispatch(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Dispatch request to appropriate handler."""
        method = request.method
        params = request.params

        try:
            if method == METHOD_COMPILE:
                result = await self._handle_compile(params)
            elif method == METHOD_ASK:
                result = await self._handle_ask(params)
            elif method == METHOD_STATUS:
                result = await self._handle_status(params)
            elif method == METHOD_STOP:
                result = await self._handle_stop()
            elif method == "daemon_status":
                result = {
                    "version": str(__version__),
                    "uptime_seconds": self.uptime_seconds,
                    "projects": self.list_projects(),
                }
            elif method == METHOD_PING:
                result = {"pong": True, "uptime": self.uptime_seconds}
            else:
                result = JSONRPCResponse.from_error(
                    code=Error.METHOD_NOT_FOUND,
                    message=f"Unknown method: {method}",
                    request_id=request.id,
                )
                return result

            return JSONRPCResponse.from_result(result, request.id)

        except Exception as e:
            logger.exception("Error handling %s request: %s", method, e)
            return JSONRPCResponse.from_error(
                code=Error.INTERNAL_ERROR,
                message=str(e),
                request_id=request.id,
            )

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    async def _handle_compile(self, params: dict) -> dict:
        """Handle index request."""
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
        """Handle search request."""
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
            raise ValueError(f"Project not indexed: {project_root}. Call index() first.")

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
