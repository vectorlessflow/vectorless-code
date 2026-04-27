"""Daemon server entry point.

Run the daemon as a background process with proper lifecycle management.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from vectorless_code._daemon_paths import (
    daemon_pid_path,
    daemon_runtime_dir,
)
from vectorless_code._version import __version__
from vectorless_code.daemon.core import Daemon

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def _write_pid_file() -> None:
    """Write current PID to the pid file."""
    runtime_dir = daemon_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    pid_path = daemon_pid_path()
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
    import threading  # noqa: F401 (needed for os._exit check)
    run_daemon()
