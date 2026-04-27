"""Simple JSON-RPC 2.0 protocol for daemon communication.

Replaces the complex msgspec + msgpack protocol with human-readable JSON.
Follows JSON-RPC 2.0 specification: https://www.jsonrpc.org/specification
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Method names (matching CLI commands)
METHOD_COMPILE = "compile"  # vcc compile
METHOD_ASK = "ask"  # vcc ask
METHOD_STATUS = "status"  # vcc status
METHOD_STOP = "stop"  # vcc daemon stop
METHOD_PING = "ping"  # health check


# ---------------------------------------------------------------------------
# Error codes (JSON-RPC 2.0)
# ---------------------------------------------------------------------------


class Error:
    """JSON-RPC error codes."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # Application-specific errors
    PROJECT_NOT_FOUND = -1
    PROJECT_NOT_INDEXED = -2
    INDEX_FAILED = -3
    SEARCH_FAILED = -4


@dataclass
class ErrorObject:
    """JSON-RPC error object."""

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"code": self.code, "message": self.message}
        if self.data is not None:
            result["data"] = self.data
        return result


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 request."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            result["params"] = self.params
        if self.id is not None:
            result["id"] = self.id
        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> JSONRPCRequest:
        """Create from dictionary (parsed JSON)."""
        return cls(
            method=data.get("method", ""),
            params=data.get("params", {}),
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )

    @classmethod
    def from_json(cls, data: str) -> JSONRPCRequest:
        """Create from JSON string."""
        return cls.from_dict(json.loads(data))


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response."""

    result: Any = None
    error: ErrorObject | None = None
    id: int | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.error is not None:
            result["error"] = self.error.to_dict()
        else:
            result["result"] = self.result
        if self.id is not None:
            result["id"] = self.id
        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> JSONRPCResponse:
        """Create from dictionary (parsed JSON)."""
        error_data = data.get("error")
        error = None
        if error_data:
            error = ErrorObject(
                code=error_data["code"],
                message=error_data["message"],
                data=error_data.get("data"),
            )

        return cls(
            result=data.get("result"),
            error=error,
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )

    @classmethod
    def from_json(cls, data: str) -> JSONRPCResponse:
        """Create from JSON string."""
        return cls.from_dict(json.loads(data))

    @classmethod
    def from_result(cls, result: Any, request_id: int | None = None) -> JSONRPCResponse:
        """Create a success response."""
        return cls(result=result, id=request_id)

    @classmethod
    def from_error(
        cls,
        code: int,
        message: str,
        data: Any = None,
        request_id: int | None = None,
    ) -> JSONRPCResponse:
        """Create an error response."""
        return cls(
            error=ErrorObject(code=code, message=message, data=data),
            id=request_id,
        )


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------


def compile_request(project_root: str, request_id: int = 1) -> JSONRPCRequest:
    """Create a compile request."""
    return JSONRPCRequest(
        method=METHOD_COMPILE,
        params={"project_root": project_root},
        id=request_id,
    )


def ask_request(
    project_root: str,
    query: str,
    limit: int = 10,
    offset: int = 0,
    request_id: int = 1,
) -> JSONRPCRequest:
    """Create an ask request."""
    return JSONRPCRequest(
        method=METHOD_ASK,
        params={
            "project_root": project_root,
            "query": query,
            "limit": limit,
            "offset": offset,
        },
        id=request_id,
    )


def status_request(project_root: str, request_id: int = 1) -> JSONRPCRequest:
    """Create a status request."""
    return JSONRPCRequest(
        method=METHOD_STATUS,
        params={"project_root": project_root},
        id=request_id,
    )


def stop_request(request_id: int = 1) -> JSONRPCRequest:
    """Create a stop request."""
    return JSONRPCRequest(
        method=METHOD_STOP,
        params={},
        id=request_id,
    )


def ping_request(request_id: int = 1) -> JSONRPCRequest:
    """Create a ping request."""
    return JSONRPCRequest(
        method=METHOD_PING,
        params={},
        id=request_id,
    )


# ---------------------------------------------------------------------------
# Response types (for CLI and MCP server)
# ---------------------------------------------------------------------------


@dataclass
class IndexingProgress:
    """Progress information during indexing."""

    files_scanned: int
    files_processed: int
    files_unchanged: int
    files_error: int


@dataclass
class CodeChunkResult:
    """A single code chunk result from search."""

    file_path: str
    source_path: str | None = None
    doc_name: str | None = None
    node_title: str | None = None
    content: str = ""
    score: float = 0.0


@dataclass
class SearchResponse:
    """Response from a search query."""

    success: bool
    results: list[CodeChunkResult] = field(default_factory=list)
    total_returned: int = 0
    offset: int = 0
    message: str | None = None
    confidence: float = 0.0


@dataclass
class ProjectStatusResponse:
    """Response from a project status query."""

    indexed: bool
    indexing: bool = False
    doc_id: str | None = None
    file_count: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    node_count: int = 0
    last_modified: float | None = None
    progress: IndexingProgress | None = None


@dataclass
class DoctorCheckResult:
    """Result from a single doctor check."""

    name: str
    ok: bool
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class DaemonStatusResponse:
    """Response from daemon status query."""

    version: str
    uptime_seconds: float
    projects: list[dict[str, Any]]


@dataclass
class PathMapping:
    """A path mapping for Docker environments."""

    source: str
    target: str


@dataclass
class DaemonEnvResponse:
    """Response from daemon environment query."""

    path_mappings: list[PathMapping]
