# Simplified Daemon Architecture for vectorless-code

## Current Problems (Cocoindex-code Copy)

### Over-engineered IPC
- `msgspec` + msgpack binary protocol
- 20+ Request/Response struct types
- Complex handshake with version checking
- Settings mtime monitoring for restart

### Unnecessary Complexity
- `multiprocessing.connection.Listener` (thread-based)
- Separate `client.py` with connection pooling
- Per-request connection model (overhead on each call)

### What vectorless-code Actually Needs
1. **MCP server**: Background process responding to AI assistant queries
2. **Cache doc_id**: Avoid recompiling on every query
3. **Auto-recompile**: Watch files and recompile on changes
4. **Concurrent queries**: Multiple clients can query simultaneously

## Simplified Architecture

### Core Principles
1. **JSON over Unix socket** (not msgpack)
2. **asyncio** (not multiprocessing.Listener)
3. **watchdog** for file monitoring
4. **Simple request/response** (method + params + id)

### New File Structure

```
src/vectorless_code/
├── daemon/
│   ├── __init__.py
│   ├── core.py           # Daemon class, ProjectRegistry
│   ├── watcher.py        # File watching with watchdog
│   ├── protocol.py       # Simple JSON protocol
│   └── server.py         # Unix socket server (asyncio)
├── client.py             # Simplified client (JSON + asyncio)
└── cli.py                # Use client
```

### Protocol Design

```python
# Request (JSON)
{
    "jsonrpc": "2.0",
    "method": "index" | "search" | "status" | "stop",
    "params": {
        "project_root": "/path/to/project",
        "query": "...",      # for search
        "limit": 5,          # for search
        # ...
    },
    "id": 1
}

# Response (JSON)
{
    "jsonrpc": "2.0",
    "result": {
        "success": true,
        "doc_id": "...",
        # ...
    },
    "error": null,
    "id": 1
}
```

### Daemon Core

```python
class Daemon:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self.projects: dict[str, ProjectState] = {}
        self.watchers: dict[str, FileWatcher] = {}
        self.server: asyncio.Server | None = None
        self._index_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        """Start the daemon server."""
        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        logger.info("Daemon listening on %s", self.socket_path)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a client connection."""
        try:
            # Read line (JSON request + newline)
            line = await reader.readline()
            if not line:
                return

            request = json.loads(line.decode())
            response = await self._dispatch(request)

            # Write response + newline
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(self, request: dict) -> dict:
        """Dispatch request to handler."""
        method = request.get("method")
        params = request.get("params", {})
        req_id = request.get("id")

        try:
            if method == "index":
                result = await self._handle_index(params)
            elif method == "search":
                result = await self._handle_search(params)
            elif method == "status":
                result = await self._handle_status(params)
            elif method == "stop":
                result = await self._handle_stop()
                self._stop_requested = True
            else:
                raise ValueError(f"Unknown method: {method}")

            return {"jsonrpc": "2.0", "result": result, "id": req_id}
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -1, "message": str(e)},
                "id": req_id,
            }

    async def _handle_index(self, params: dict) -> dict:
        """Handle index request."""
        project_root = params["project_root"]
        lock = self._index_locks.setdefault(project_root, asyncio.Lock())

        async with lock:
            # Get or create project state
            if project_root not in self.projects:
                self.projects[project_root] = ProjectState(
                    root=Path(project_root),
                )

            project = self.projects[project_root]

            # Compile
            result = await compile_project(
                project_root=Path(project_root),
                user_settings=self._user_settings,
            )

            # Update state
            project.doc_id = result.doc_id
            project.last_index_time = time.monotonic()

            # Start file watcher if not running
            if project_root not in self.watchers:
                watcher = FileWatcher(
                    path=Path(project_root),
                    on_change=lambda: self._schedule_reindex(project_root),
                )
                watcher.start()
                self.watchers[project_root] = watcher

            return {
                "success": result.ok,
                "doc_id": result.doc_id,
                "file_count": result.file_count,
                "total_lines": result.total_lines,
            }

    async def _handle_search(self, params: dict) -> dict:
        """Handle search request."""
        project_root = params["project_root"]
        query = params["query"]
        limit = params.get("limit", 10)

        if project_root not in self.projects:
            raise ValueError(f"Project not indexed: {project_root}")

        project = self.projects[project_root]
        if not project.doc_id:
            raise ValueError(f"Project has no doc_id: {project_root}")

        # Search via vectorless
        output = await ask_codebase(
            question=query,
            doc_ids=[project.doc_id],
            user_settings=self._user_settings,
        )

        results = [
            {
                "file_path": ev.source_path or "",
                "node_title": ev.node_title,
                "content": ev.content,
                "score": 0.0,  # vectorless doesn't return per-evidence scores
            }
            for ev in output.evidence
        ]

        return {
            "success": True,
            "results": results[:limit],
            "confidence": output.confidence,
        }

    def _schedule_reindex(self, project_root: str) -> None:
        """Schedule a reindex for the project."""
        # Debounce: wait 1s after last change before reindexing
        if project_root in self._reindex_timers:
            self._reindex_timers[project_root].cancel()

        async def _do_reindex() -> None:
            await asyncio.sleep(1.0)
            await self._handle_index({"project_root": project_root})

        task = asyncio.create_task(_do_reindex())
        self._reindex_timers[project_root] = task
```

### File Watcher

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent


class ChangeHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[], None]):
        self._callback = callback
        self._debounce_timer: threading.Timer | None = None

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return

        # Filter to source files only
        if not any(event.src_path.endswith(ext) for ext in SOURCE_EXTS):
            return

        # Debounce: wait 500ms after last change
        if self._debounce_timer:
            self._debounce_timer.cancel()

        self._debounce_timer = threading.Timer(0.5, self._callback)
        self._debounce_timer.start()


class FileWatcher:
    def __init__(self, path: Path, on_change: Callable[[], None]):
        self.path = path
        self.observer = Observer()
        self.observer.schedule(
            ChangeHandler(on_change),
            str(path),
            recursive=True,
        )

    def start(self) -> None:
        self.observer.start()

    def stop(self) -> None:
        self.observer.stop()
        self.observer.join()
```

### Simplified Client

```python
class DaemonClient:
    def __init__(self, socket_path: Path | None = None):
        self.socket_path = socket_path or daemon_socket_path()
        self._request_id = 0

    async def _call(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and return the result."""
        self._request_id += 1

        reader, writer = await asyncio.open_unix_connection(
            str(self.socket_path)
        )

        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._request_id,
        }

        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()

        line = await reader.readline()
        writer.close()
        await writer.wait_closed()

        response = json.loads(line.decode())

        if "error" in response:
            raise RuntimeError(response["error"]["message"])

        return response["result"]

    async def index(self, project_root: str) -> dict:
        return await self._call("index", {"project_root": project_root})

    async def search(
        self,
        project_root: str,
        query: str,
        limit: int = 10,
    ) -> dict:
        return await self._call(
            "search",
            {
                "project_root": project_root,
                "query": query,
                "limit": limit,
            },
        )

    async def status(self, project_root: str) -> dict:
        return await self._call("status", {"project_root": project_root})

    async def stop(self) -> dict:
        return await self._call("stop", {})
```

## Docker Simplification

With the simplified daemon, Docker becomes much simpler:

```dockerfile
FROM python:3.12-slim

RUN pip install uv
RUN useradd -m -u 1000 vcc

WORKDIR /workspace

COPY . /vcc-src
RUN uv pip install --system /vcc-src

USER vcc
ENV VECTORLESS_DAEMON_SUPERVISED=1

# Simple entrypoint: just run the daemon
CMD ["python", "-m", "vectorless_code.daemon.server"]
```

```bash
# No need for complex while loops in entrypoint.sh
# The daemon handles its own lifecycle
```

## Migration Plan

1. **Phase 1**: Create new `daemon/` package alongside existing code
2. **Phase 2**: Update CLI to use new client (feature flag)
3. **Phase 3**: Update MCP server to use new client
4. **Phase 4**: Remove old `daemon.py`, `client.py`, `protocol.py`
5. **Phase 5**: Simplify Docker configuration

## Benefits

| Aspect | Current | Simplified |
|--------|---------|------------|
| Lines of code | ~1500 | ~600 |
| Dependencies | msgspec, multiprocessing | watchdog, asyncio |
| Protocol complexity | msgpack binary | JSON text |
| File monitoring | None | watchdog |
| Docker entrypoint | 50 lines while loop | 1 line CMD |
| Debugging | Binary protocol | Human-readable JSON |
