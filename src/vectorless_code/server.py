"""MCP server for codebase indexing and querying.

Supports two modes:
1. Daemon-backed: ``create_mcp_server(project_root)`` — lightweight MCP
   server that delegates to the daemon via per-request client functions.
2. Legacy entry point: ``main()`` — backward-compatible ``vectorless-code`` CLI that
   auto-creates settings from env vars and delegates to the daemon.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

try:
    from fastmcp import FastMCP
    from pydantic import BaseModel, Field

    FASTMCP_AVAILABLE = True
except ImportError:
    FASTMCP_AVAILABLE = False
    FastMCP = None  # type: ignore
    BaseModel = object  # type: ignore
    Field = None  # type: ignore

_MCP_INSTRUCTIONS = (
    "Code search and codebase understanding tools."
    "\n"
    "Use when you need to find code, understand how something works,"
    " locate implementations, or explore an unfamiliar codebase."
    "\n"
    "Provides semantic search that understands meaning --"
    " unlike grep or text matching,"
    " it finds relevant code even when exact keywords are unknown."
)


# === Pydantic Models for Tool Inputs/Outputs ===


if FASTMCP_AVAILABLE:

    class CodeChunkResult(BaseModel):  # type: ignore
        """A single code chunk result."""

        file_path: str = Field(description="Relative path to the file")
        source_path: str | None = Field(default=None, description="Full source path")
        doc_name: str | None = Field(default=None, description="Document name")
        node_title: str | None = Field(default=None, description="Node title")
        content: str = Field(description="The code content")
        score: float = Field(description="Confidence score (0-1, higher is better)")

    class SearchResultModel(BaseModel):  # type: ignore
        """Result from search tool."""

        success: bool
        results: list[CodeChunkResult] = Field(default_factory=list)
        total_returned: int = Field(default=0)
        offset: int = Field(default=0)
        message: str | None = None
        confidence: float = Field(default=0.0)


# === Daemon-backed MCP server factory ===


def create_mcp_server(project_root: str) -> FastMCP:
    """Create a lightweight MCP server that delegates to the daemon."""
    if not FASTMCP_AVAILABLE:
        raise RuntimeError(
            "FastMCP is not installed. Install it with: pip install fastmcp"
        )

    mcp = FastMCP("vectorless-code", instructions=_MCP_INSTRUCTIONS)

    @mcp.tool(
        name="search",
        description=(
            "Semantic code search across the entire codebase"
            " -- finds code by meaning, not just text matching."
            " Use this instead of grep/glob when you need to find implementations,"
            " understand how features work,"
            " or locate related code without knowing exact names or keywords."
            " Accepts natural language queries"
            " (e.g., 'authentication logic', 'database connection handling')"
            " or code snippets."
            " Returns matching code chunks with file paths,"
            " and relevance scores."
            " Start with a small limit (e.g., 5);"
            " if most results look relevant, use offset to paginate for more."
        ),
    )
    async def search(
        query: str = Field(
            description=(
                "Natural language query or code snippet to search for."
                " Examples: 'error handling middleware',"
                " 'how are users authenticated',"
                " 'database connection pool',"
                " or paste a code snippet to find similar code."
            )
        ),
        limit: int = Field(
            default=5,
            ge=1,
            le=100,
            description="Maximum number of results to return (1-100)",
        ),
        offset: int = Field(
            default=0,
            ge=0,
            description="Number of results to skip for pagination",
        ),
        refresh_index: bool = Field(
            default=True,
            description=(
                "Whether to incrementally update the index before searching."
                " Set to False for faster consecutive queries"
                " when the codebase hasn't changed."
            ),
        ),
    ) -> SearchResultModel:
        """Query the codebase index via the daemon."""
        from . import client as _client

        loop = asyncio.get_event_loop()
        try:
            if refresh_index:
                await loop.run_in_executor(None, lambda: _client.index(project_root))

            resp = await loop.run_in_executor(
                None,
                lambda: _client.search(
                    project_root=project_root,
                    query=query,
                    doc_ids=None,
                    limit=limit,
                    offset=offset,
                ),
            )
            return SearchResultModel(
                success=resp.success,
                results=[
                    CodeChunkResult(
                        file_path=r.file_path,
                        source_path=r.source_path,
                        doc_name=r.doc_name,
                        node_title=r.node_title,
                        content=r.content,
                        score=r.score,
                    )
                    for r in resp.results
                ],
                total_returned=resp.total_returned,
                offset=resp.offset,
                message=resp.message,
                confidence=resp.confidence,
            )
        except Exception as e:
            return SearchResultModel(success=False, message=f"Query failed: {e!s}")

    return mcp


# Keep the old `mcp` global for backward compatibility
mcp: FastMCP | None = None
