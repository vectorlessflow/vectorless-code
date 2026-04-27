"""Response types for vcc daemon communication.

Shared data structures for CLI, MCP server, and daemon responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
class DaemonStatusResponse:
    """Response from daemon status query."""

    version: str
    uptime_seconds: float
    projects: list[dict[str, Any]]


@dataclass
class DoctorCheckResult:
    """Result from a single doctor check."""

    name: str
    ok: bool
    details: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
