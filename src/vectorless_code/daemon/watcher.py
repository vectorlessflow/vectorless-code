"""File watcher for auto-recompilation on code changes.

Uses watchdog to monitor project directories and trigger recompilation
when source files are modified.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Source file extensions to monitor
SOURCE_EXTENSIONS = {
    # Python
    ".py", ".pyi",
    # JavaScript/TypeScript
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    # Rust
    ".rs",
    # Go
    ".go",
    # Java
    ".java",
    # C/C++
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    # Ruby
    ".rb",
    # Kotlin
    ".kt", ".kts",
    # Scala
    ".scala",
    # Others
    ".php", ".sh", ".bash", ".lua", ".sql",
}


class _ChangeHandler:
    """Internal watchdog event handler with debouncing."""

    def __init__(self, callback: Callable[[], None], debounce_secs: float = 0.5):
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
        if not any(path.endswith(ext) for ext in SOURCE_EXTENSIONS):
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

            self._timer = threading.Timer(
                self._debounce_secs,
                self._callback,
            )
            self._timer.start()

    def stop(self) -> None:
        """Cancel any pending timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class FileWatcher:
    """Monitor a directory for file changes and trigger callbacks.

    Uses watchdog's Observer pattern to watch a directory tree.
    Debounces rapid changes to avoid excessive recompilations.

    Example:
        ```python
        def on_change():
            print("Files changed, recompiling...")

        watcher = FileWatcher(Path("/my/project"), on_change)
        watcher.start()

        # Later...
        watcher.stop()
        ```
    """

    def __init__(
        self,
        path: Path,
        on_change: Callable[[], None],
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
        """Start watching the directory.

        This method blocks briefly while setting up the observer.
        The actual watching happens in a background thread.
        """
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

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        self.stop()
