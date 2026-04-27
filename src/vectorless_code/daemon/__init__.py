"""Simplified daemon package for vectorless-code.

Uses asyncio + Unix socket + JSON-RPC instead of the complex
msgspec + multiprocessing.Listener architecture copied from cocoindex-code.
"""

from vectorless_code.daemon.core import Daemon
from vectorless_code.daemon.protocol import JSONRPCRequest, JSONRPCResponse
from vectorless_code.daemon.watcher import FileWatcher

__all__ = ["Daemon", "FileWatcher", "JSONRPCRequest", "JSONRPCResponse"]
