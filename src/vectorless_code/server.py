"""MCP server for codebase indexing and querying.

Supports two modes:
1. Daemon-backed MCP server - delegates to the daemon via client functions.
2. Legacy entry point: ``main()`` - backward-compatible CLI that auto-creates
   settings from env vars and delegates to the daemon.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

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


class CodeChunkResult(BaseModel):
    """A single code chunk result."""

    file_path: str = Field(description="Relative path to the file")
    source_path: str | None = Field(default=None, description="Full source path")
    doc_name: str | None = Field(default=None, description="Document name")
    node_title: str | None = Field(default=None, description="Node title")
    content: str = Field(description="The code content")
    score: float = Field(description="Confidence score (0-1, higher is better)")


class SearchResultModel(BaseModel):
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


# === Backward-compatible entry point ===


def main() -> None:
    """Backward-compatible entry point for ``vectorless-code`` CLI.

    Auto-detects/creates settings from env vars, then delegates to daemon.
    """
    import argparse

    from .settings import (
        find_project_root,
        load_project_settings,
        save_initial_settings,
        user_settings_path,
    )

    parser = argparse.ArgumentParser(
        prog="vectorless-code",
        description="MCP server for codebase indexing and querying.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the MCP server (default)")
    subparsers.add_parser("index", help="Build/refresh the index and report stats")
    args = parser.parse_args()

    # --- Discover project root ---
    cwd = Path.cwd()
    project_root = find_project_root(cwd)

    if project_root is None:
        # Try env var
        env_root = os.environ.get("VECTORLESS_CODE_ROOT_PATH")
        if env_root:
            project_root = Path(env_root).resolve()
        else:
            # Use current directory
            project_root = cwd

    # --- Auto-create project settings if needed ---
    proj_settings_file = project_root / ".vectorless_code" / "settings.yml"
    if not proj_settings_file.is_file():
        save_initial_settings(project_root)

    # --- Ensure user settings exist ---
    user_file = user_settings_path()
    if not user_file.is_file():
        import sys

        print(
            "Error: User settings not found. Run `vcc init` to configure.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Delegate to daemon ---
    from . import client as _client
    from .protocol import IndexingProgress

    if args.command == "index":
        import sys

        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner

        from .cli import _format_progress

        err_console = Console(stderr=True)
        last_progress_line: str | None = None

        with Live(Spinner("dots", "Indexing..."), console=err_console, transient=True) as live:

            def _on_waiting() -> None:
                live.update(
                    Spinner(
                        "dots",
                        "Another indexing is ongoing, waiting for it to finish...",
                    )
                )

            def _on_progress(progress: IndexingProgress) -> None:
                nonlocal last_progress_line
                last_progress_line = f"Indexing: {_format_progress(progress)}"
                live.update(Spinner("dots", last_progress_line))

            resp = _client.index(
                str(project_root), on_progress=_on_progress, on_waiting=_on_waiting
            )

        if last_progress_line is not None:
            print(last_progress_line, file=sys.stderr)

        if resp.success:
            st = _client.project_status(str(project_root))
            print("\nIndex stats:")
            print(f"  Files:  {st.file_count}")
            print(f"  Lines:  {st.total_lines}")
            print(f"  Size:   {st.total_bytes} bytes")
            if st.languages:
                print("  Languages:")
                for lang, count in sorted(st.languages.items(), key=lambda x: -x[1]):
                    print(f"    {lang}: {count}")
        else:
            print(f"Indexing failed: {resp.message}")
    else:
        # Default: run MCP server
        mcp_server = create_mcp_server(str(project_root))

        async def _serve() -> None:
            from .client import _bg_index

            asyncio.create_task(_bg_index(str(project_root)))
            await mcp_server.run_stdio_async()

        asyncio.run(_serve())
