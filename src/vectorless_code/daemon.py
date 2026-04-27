"""Daemon process: listener loop, project registry, request dispatch."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from multiprocessing.connection import Connection, Listener
from pathlib import Path
from typing import Any

from ._daemon_paths import (
    connection_family,
    daemon_log_path,
    daemon_pid_path,
    daemon_runtime_dir,
    daemon_socket_path,
)
from ._version import __version__
from .compile import CompileResult, compile_project
from .engine import create_engine
from .protocol import (
    DaemonEnvRequest,
    DaemonEnvResponse,
    DaemonProjectInfo,
    DaemonStatusRequest,
    DaemonStatusResponse,
    DoctorCheckResult,
    DoctorRequest,
    DoctorResponse,
    DoctorStreamResponse,
    ErrorResponse,
    HandshakeRequest,
    HandshakeResponse,
    IndexRequest,
    IndexStreamResponse,
    IndexWaitingNotice,
    ProjectStatusRequest,
    ProjectStatusResponse,
    RemoveProjectRequest,
    RemoveProjectResponse,
    Request,
    Response,
    SearchRequest,
    SearchResponse,
    SearchStreamResponse,
    StopRequest,
    StopResponse,
    decode_request,
    encode_response,
)
from .settings import UserSettings, find_project_root, get_host_path_mappings, load_project_settings, load_user_settings, normalize_path, user_settings_path, global_settings_mtime_us

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project Registry
# ---------------------------------------------------------------------------


class ProjectRegistry:
    """Cache of loaded projects, keyed by project root path."""

    _projects: dict[str, Project]
    _user_settings: UserSettings | None

    def __init__(self, user_settings: UserSettings | None = None) -> None:
        self._projects = {}
        self._user_settings = user_settings

    async def get_project(self, project_root: str) -> Project:
        """Get or create a Project for the given root."""
        if project_root not in self._projects:
            root = Path(project_root)
            user_settings = self._user_settings or load_user_settings()
            project = await Project.create(root, user_settings)
            self._projects[project_root] = project
        return self._projects[project_root]

    def remove_project(self, project_root: str) -> bool:
        """Remove a project from the registry. Returns True if it was loaded."""
        project = self._projects.pop(project_root, None)
        if project is not None:
            project.close()
            return True
        return False

    def close_all(self) -> None:
        """Close all loaded projects and release resources."""
        for project in self._projects.values():
            project.close()
        self._projects.clear()

    def list_projects(self) -> list[DaemonProjectInfo]:
        """List all loaded projects with their indexing state."""
        return [
            DaemonProjectInfo(
                project_root=root,
                indexing=project._index_lock.locked(),
            )
            for root, project in self._projects.items()
        ]


# ---------------------------------------------------------------------------
# Connection handler
# ---------------------------------------------------------------------------


async def handle_connection(
    conn: Connection,
    registry: ProjectRegistry,
    start_time: float,
    on_shutdown: Callable[[], None],
    settings_mtime_us: int | None,
    settings_env_names: list[str],
    handshake_warnings: list[str],
) -> None:
    """Handle a single client connection (per-request model)."""
    loop = asyncio.get_event_loop()
    try:
        # 1. Handshake
        data: bytes = await loop.run_in_executor(None, conn.recv_bytes)
        req = decode_request(data)

        if not isinstance(req, HandshakeRequest):
            conn.send_bytes(
                encode_response(ErrorResponse(message="First message must be a handshake"))
            )
            return

        ok = req.version == __version__
        conn.send_bytes(
            encode_response(
                HandshakeResponse(
                    ok=ok,
                    daemon_version=__version__,
                    global_settings_mtime_us=settings_mtime_us,
                    warnings=list(handshake_warnings),
                )
            )
        )
        if not ok:
            return

        # 2. Single request
        data = await loop.run_in_executor(None, conn.recv_bytes)
        req = decode_request(data)

        result = await _dispatch(req, registry, start_time, on_shutdown, settings_env_names)
        if isinstance(result, AsyncIterator):
            try:
                async for resp in result:
                    conn.send_bytes(encode_response(resp))
            except Exception as exc:
                logger.exception("Error during streaming response")
                conn.send_bytes(encode_response(ErrorResponse(message=str(exc))))
        else:
            conn.send_bytes(encode_response(result))
    except (EOFError, OSError, asyncio.CancelledError):
        pass
    except Exception:
        logger.exception("Error handling connection")
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _search_with_wait(
    project: Project, req: SearchRequest
) -> AsyncIterator[SearchStreamResponse]:
    """Stream search response, waiting for ongoing indexing first."""
    yield IndexWaitingNotice()
    await project.wait_for_indexing_done()
    try:
        results = await project.search(
            query=req.query,
            doc_ids=req.doc_ids,
            limit=req.limit,
            offset=req.offset,
        )
        yield SearchResponse(
            success=True,
            results=results,
            total_returned=len(results),
            offset=req.offset,
        )
    except Exception as e:
        yield ErrorResponse(message=str(e))


async def _handle_doctor(
    req: DoctorRequest,
    registry: ProjectRegistry,
) -> AsyncIterator[DoctorStreamResponse]:
    """Run doctor checks sequentially, yielding results as they complete."""
    if req.project_root is None:
        # Global checks
        yield DoctorResponse(
            result=await _check_user_settings(registry._user_settings)
        )
    else:
        # Project checks
        yield DoctorResponse(result=await _check_project_settings(req.project_root))
        yield DoctorResponse(result=await _check_index_status(req.project_root))

    # Final marker
    yield DoctorResponse(
        result=DoctorCheckResult(name="done", ok=True, details=[], errors=[]),
        final=True,
    )


async def _check_user_settings(user_settings: UserSettings | None) -> DoctorCheckResult:
    """Check if user settings are configured."""
    name = "User Settings"
    if user_settings is None:
        return DoctorCheckResult(
            name=name,
            ok=False,
            details=[],
            errors=["No user settings found. Run `vcc init` to set up."],
        )

    details: list[str] = []
    errors: list[str] = []

    if user_settings.api_key:
        details.append(f"API key: configured")
    else:
        errors.append("API key: not set")

    if user_settings.model:
        details.append(f"Model: {user_settings.model}")
    else:
        errors.append("Model: not set")

    if user_settings.endpoint:
        details.append(f"Endpoint: {user_settings.endpoint}")
    else:
        errors.append("Endpoint: not set")

    return DoctorCheckResult(
        name=name,
        ok=not errors,
        details=details,
        errors=errors,
    )


async def _check_project_settings(project_root_str: str) -> DoctorCheckResult:
    """Check if project settings exist."""
    from .settings import project_settings_path

    project_root = Path(project_root_str)
    settings_file = project_settings_path(project_root)

    if not settings_file.is_file():
        return DoctorCheckResult(
            name="Project Settings",
            ok=False,
            details=[],
            errors=[f"Project settings not found: {settings_file}"],
        )

    try:
        ps = load_project_settings(project_root)
        details = [
            f"Include patterns: {len(ps.include_patterns)}",
            f"Exclude patterns: {len(ps.exclude_patterns)}",
        ]
        return DoctorCheckResult(
            name="Project Settings",
            ok=True,
            details=details,
            errors=[],
        )
    except Exception as e:
        return DoctorCheckResult(
            name="Project Settings",
            ok=False,
            details=[],
            errors=[str(e)],
        )


async def _check_index_status(project_root_str: str) -> DoctorCheckResult:
    """Check index status."""
    project_root = Path(project_root_str)
    cache_dir = project_root / ".vectorless_code" / "cache"

    details = [f"Cache dir: {cache_dir}"]
    errors: list[str] = []

    if not cache_dir.exists():
        details.append("Index not created yet.")
        return DoctorCheckResult(
            name="Index Status",
            ok=True,
            details=details,
            errors=errors,
        )

    hashes_file = cache_dir / "hashes.json"
    nodes_file = cache_dir / "parsed_nodes.json"

    if hashes_file.exists():
        import json

        try:
            hashes = json.loads(hashes_file.read_text())
            details.append(f"Hashed files: {len(hashes)}")
        except Exception:
            errors.append("Could not read hashes.json")

    if nodes_file.exists():
        details.append("Parsed nodes cache exists")

    return DoctorCheckResult(
        name="Index Status",
        ok=not errors,
        details=details,
        errors=errors,
    )


async def _dispatch(
    req: Request,
    registry: ProjectRegistry,
    start_time: float,
    on_shutdown: Callable[[], None],
    settings_env_names: list[str],
) -> (
    Response
    | AsyncIterator[IndexStreamResponse]
    | AsyncIterator[SearchStreamResponse]
    | AsyncIterator[DoctorStreamResponse]
):
    """Dispatch a request to the appropriate handler."""
    try:
        if isinstance(req, IndexRequest):
            project = await registry.get_project(req.project_root)
            return project.stream_index()

        if isinstance(req, SearchRequest):
            project = await registry.get_project(req.project_root)
            await project.ensure_indexing_started()

            if project.should_wait_for_indexing:
                return _search_with_wait(project, req)

            results = await project.search(
                query=req.query,
                doc_ids=req.doc_ids,
                limit=req.limit,
                offset=req.offset,
            )
            return SearchResponse(
                success=True,
                results=results,
                total_returned=len(results),
                offset=req.offset,
            )

        if isinstance(req, ProjectStatusRequest):
            project = await registry.get_project(req.project_root)
            await project.ensure_indexing_started()
            return project.get_status()

        if isinstance(req, DaemonStatusRequest):
            return DaemonStatusResponse(
                version=__version__,
                uptime_seconds=time.monotonic() - start_time,
                projects=registry.list_projects(),
            )

        if isinstance(req, RemoveProjectRequest):
            registry.remove_project(req.project_root)
            return RemoveProjectResponse(ok=True)

        if isinstance(req, StopRequest):
            on_shutdown()
            return StopResponse(ok=True)

        if isinstance(req, DaemonEnvRequest):
            from .protocol import DbPathMappingEntry

            return DaemonEnvResponse(
                env_names=sorted(os.environ.keys()),
                settings_env_names=settings_env_names,
                path_mappings=[
                    DbPathMappingEntry(source=str(m[0]), target=str(m[1]))
                    for m in get_host_path_mappings()
                ],
            )

        if isinstance(req, DoctorRequest):
            return _handle_doctor(req, registry)

        return ErrorResponse(message=f"Unknown request type: {type(req).__name__}")
    except Exception as e:
        logger.exception("Error dispatching request")
        return ErrorResponse(message=str(e))


# ---------------------------------------------------------------------------
# Daemon main
# ---------------------------------------------------------------------------


def run_daemon() -> None:
    """Main entry point for the daemon process (blocking)."""
    daemon_runtime_dir().mkdir(parents=True, exist_ok=True)

    settings_mtime_us = global_settings_mtime_us()
    user_settings: UserSettings | None = None
    settings_env_keys: list[str] = []
    handshake_warnings: list[str] = []

    if user_settings_path().is_file():
        user_settings = load_user_settings()
        settings_env_keys = list(user_settings.__dict__.keys())

    # Write PID file
    pid_path = daemon_pid_path()
    pid_path.write_text(str(os.getpid()))

    # Set up logging to file
    log_path = daemon_log_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(str(log_path), mode="w"), logging.StreamHandler()],
        force=True,
    )

    logger.info("Daemon starting (PID %d, version %s)", os.getpid(), __version__)

    start_time = time.monotonic()
    registry = ProjectRegistry(user_settings)

    sock_path = daemon_socket_path()
    if sys.platform != "win32":
        try:
            Path(sock_path).unlink(missing_ok=True)
        except Exception:
            pass

    listener = Listener(str(sock_path), family=connection_family())
    logger.info("Listening on %s", sock_path)

    loop = asyncio.new_event_loop()
    tasks: set[asyncio.Task[Any]] = set()

    def _request_shutdown() -> None:
        loop.stop()

    def _spawn_handler(conn: Connection) -> None:
        task = loop.create_task(
            handle_connection(
                conn,
                registry,
                start_time,
                _request_shutdown,
                settings_mtime_us,
                settings_env_keys,
                handshake_warnings,
            )
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    # Handle signals for graceful shutdown
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _request_shutdown)
    except (RuntimeError, NotImplementedError):
        pass

    def _accept_loop() -> None:
        while True:
            try:
                conn = listener.accept()
                loop.call_soon_threadsafe(_spawn_handler, conn)
            except OSError:
                break

    accept_thread = threading.Thread(target=_accept_loop, daemon=True)
    accept_thread.start()

    try:
        loop.run_forever()
    finally:
        listener.close()

        for task in tasks:
            task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        registry.close_all()
        loop.close()

        if sys.platform != "win32":
            try:
                Path(sock_path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            stored = pid_path.read_text().strip()
            if stored == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except Exception:
            pass

        logger.info("Daemon stopped")

        if threading.current_thread() is threading.main_thread():
            os._exit(0)


# ---------------------------------------------------------------------------
# Project class
# ---------------------------------------------------------------------------


class Project:
    """Wraps a vectorless project with indexing and search capabilities."""

    _project_root: Path
    _user_settings: UserSettings
    _index_lock: asyncio.Lock
    _initial_index_done: asyncio.Event
    _indexing_stats: Any  # IndexingProgress
    _doc_ids: list[str] | None

    def __init__(self, project_root: Path, user_settings: UserSettings) -> None:
        self._project_root = project_root
        self._user_settings = user_settings
        self._index_lock = asyncio.Lock()
        self._initial_index_done = asyncio.Event()
        self._indexing_stats = None
        self._doc_ids = None

    def close(self) -> None:
        """Close project resources."""
        pass

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def run_index(
        self,
        on_progress: Callable[[Any], None] | None = None,
        on_started: asyncio.Event | None = None,
    ) -> None:
        """Acquire the index lock, run indexing, and release."""
        async with self._index_lock:
            from .protocol import IndexingProgress

            self._indexing_stats = IndexingProgress(
                files_scanned=0,
                files_processed=0,
                files_unchanged=0,
                files_error=0,
                total_nodes=0,
            )
            if on_started is not None:
                on_started.set()
            await self._run_index_inner(on_progress=on_progress)

    async def _run_index_inner(
        self,
        on_progress: Callable[[Any], None] | None = None,
    ) -> None:
        """Run indexing (lock must already be held)."""
        try:
            result = await compile_project(
                self._project_root,
                settings=None,
                user_settings=self._user_settings,
            )

            from .protocol import IndexingProgress

            self._indexing_stats = IndexingProgress(
                files_scanned=result.file_count,
                files_processed=result.file_count,
                files_unchanged=0,
                files_error=0 if result.ok else 1,
                total_nodes=result.file_count,
            )

            if on_progress is not None:
                on_progress(self._indexing_stats)

            if result.doc_id:
                self._doc_ids = [result.doc_id]

        finally:
            self._initial_index_done.set()
            self._indexing_stats = None

    async def ensure_indexing_started(self) -> None:
        """Kick off background indexing and wait until it has actually started."""
        if self._initial_index_done.is_set() or self._index_lock.locked():
            return
        started = asyncio.Event()
        asyncio.create_task(self.run_index(on_started=started))
        await started.wait()

    async def stream_index(self) -> AsyncIterator[IndexStreamResponse]:
        """Run indexing, streaming progress updates and a final IndexResponse."""
        from .protocol import IndexingProgress, IndexResponse

        if self._index_lock.locked():
            yield IndexWaitingNotice()

        progress_queue: asyncio.Queue[Any] = asyncio.Queue()
        index_task = asyncio.create_task(
            self.run_index(on_progress=lambda p: progress_queue.put_nowait(p))
        )

        try:
            while not index_task.done():
                try:
                    progress = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                    from .protocol import IndexProgressUpdate

                    yield IndexProgressUpdate(progress=progress)
                except TimeoutError:
                    continue

            while not progress_queue.empty():
                from .protocol import IndexProgressUpdate

                yield IndexProgressUpdate(progress=progress_queue.get_nowait())

            index_task.result()
            yield IndexResponse(success=True)
        except GeneratorExit:
            return
        except Exception as e:
            yield IndexResponse(success=False, message=str(e))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @property
    def should_wait_for_indexing(self) -> bool:
        """True if indexing has been started but not yet completed."""
        return not self._initial_index_done.is_set()

    async def wait_for_indexing_done(self) -> None:
        """Wait until initial indexing is complete and no indexing is running."""
        await self._initial_index_done.wait()
        if self._index_lock.locked():
            async with self._index_lock:
                pass

    async def search(
        self,
        query: str,
        doc_ids: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> list[Any]:
        """Search within this project."""
        from .ask import ask_codebase
        from .protocol import SearchResult

        doc_ids = doc_ids or self._doc_ids

        output = await ask_codebase(
            question=query,
            doc_ids=doc_ids,
            user_settings=self._user_settings,
            on_progress=lambda msg: None,
        )

        results: list[SearchResult] = []
        for ev in output.evidence:
            results.append(
                SearchResult(
                    file_path=ev.source_path or "",
                    source_path=ev.source_path,
                    doc_name=ev.doc_name,
                    node_title=ev.node_title,
                    content=ev.content,
                    start_line=None,
                    end_line=None,
                    score=output.confidence,
                )
            )

        return results[offset : offset + limit]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> ProjectStatusResponse:
        """Get index stats."""
        from .protocol import IndexingProgress, ProjectStatusResponse

        cache_dir = self._project_root / ".vectorless_code" / "cache"

        file_count = 0
        total_lines = 0
        total_bytes = 0
        languages: dict[str, int] = {}
        doc_id = self._doc_ids[0] if self._doc_ids else None

        hashes_file = cache_dir / "hashes.json"
        if hashes_file.exists():
            import json

            try:
                hashes = json.loads(hashes_file.read_text())
                file_count = len(hashes)
            except Exception:
                pass

        is_indexing = self._index_lock.locked()
        progress = self._indexing_stats if is_indexing else None

        return ProjectStatusResponse(
            indexing=is_indexing,
            file_count=file_count,
            total_lines=total_lines,
            total_bytes=total_bytes,
            languages=languages,
            progress=progress,
            doc_id=doc_id,
        )

    @staticmethod
    async def create(
        project_root: Path,
        user_settings: UserSettings,
    ) -> Project:
        """Create a project."""
        result = Project.__new__(Project)
        result._project_root = project_root
        result._user_settings = user_settings
        result._index_lock = asyncio.Lock()
        result._initial_index_done = asyncio.Event()
        result._indexing_stats = None
        result._doc_ids = None
        return result
