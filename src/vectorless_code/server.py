"""MCP server for codebase indexing and querying.

Supports two modes:
1. Daemon-backed MCP server - delegates to the daemon via client functions.
2. Legacy entry point: ``main()`` - backward-compatible CLI that auto-creates
   settings from env vars and delegates to the daemon.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from vectorless_code.client import DaemonClient

logger = logging.getLogger(__name__)

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


class AskResultModel(BaseModel):
    """Result from ask tool."""

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
        name="ask",
        description=(
            "Ask questions about the codebase using semantic understanding."
            " Use this instead of grep/glob when you need to find implementations,"
            " understand how features work,"
            " or locate related code without knowing exact names or keywords."
            " Accepts natural language questions"
            " (e.g., 'how are users authenticated', 'where is the error handling')"
            " or code snippets."
            " Returns matching code chunks with file paths,"
            " and relevance scores."
            " Start with a small limit (e.g., 5);"
            " if most results look relevant, use offset to paginate for more."
        ),
    )
    async def ask(
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
                "Whether to re-index the project before searching if it's stale."
                " Disable to save time if you're confident the index is up-to-date."
            ),
        ),
    ) -> AskResultModel:
        """Ask questions about the codebase using semantic understanding."""
        client = DaemonClient()
        try:
            # First check project status
            status_result = await client.status(project_root)
            if not status_result.get('indexed', False):
                if refresh_index:
                    # Compile the project first
                    compile_result = await client.compile(project_root)
                    if not compile_result.get("success"):
                        return AskResultModel(
                            success=False,
                            message=compile_result.get("message", "Compilation failed"),
                        )
                else:
                    return AskResultModel(success=False, message="Project not compiled")

            # Perform the search
            search_result = await client.ask(
                project_root=project_root,
                query=query,
                limit=limit,
                offset=offset,
            )

            # Convert results to the expected format
            results = [
                CodeChunkResult(
                    file_path=r.get('file_path', ''),
                    source_path=r.get('source_path'),
                    doc_name=r.get('doc_name'),
                    node_title=r.get('node_title'),
                    content=r.get('content', ''),
                    score=r.get('score', 0.0)
                )
                for r in search_result.get('results', [])
            ]

            return AskResultModel(
                success=search_result.get('success', False),
                results=results,
                total_returned=len(results),
                message=search_result.get('message'),
                confidence=search_result.get('confidence', 0.0)
            )
        except Exception as e:
            logger.error(f"Error in ask tool: {e}")
            return AskResultModel(success=False, message=str(e))

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
        save_initial_settings,
        user_settings_path,
    )

    parser = argparse.ArgumentParser(
        prog="vectorless-code",
        description="MCP server for codebase compilation and querying.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the MCP server (default)")
    subparsers.add_parser("compile", help="Build/refresh the compilation index and report stats")
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

    if args.command == "compile":
        import sys

        from rich.console import Console
        from rich.live import Live
        from rich.spinner import Spinner

        from .cli import _format_progress

        err_console = Console(stderr=True)
        last_progress_line: str | None = None

        with Live(Spinner("dots", "Compiling..."), console=err_console, transient=True) as live:

            def _on_waiting() -> None:
                live.update(
                    Spinner(
                        "dots",
                        "Another compilation is ongoing, waiting for it to finish...",
                    )
                )

            def _on_progress(progress: IndexingProgress) -> None:
                nonlocal last_progress_line
                last_progress_line = f"Compiling: {_format_progress(progress)}"
                live.update(Spinner("dots", last_progress_line))

            resp = _client.compile(
                str(project_root), on_progress=_on_progress, on_waiting=_on_waiting
            )

        if last_progress_line is not None:
            print(last_progress_line, file=sys.stderr)

        if resp.success:
            st = _client.status(str(project_root))
            print("\nCompilation stats:")
            print(f"  Files:  {st.get('file_count', 0)}")
            print(f"  Lines:  {st.get('total_lines', 0)}")
            print(f"  Size:   {st.get('total_bytes', 0)} bytes")
            languages = st.get('languages', {})
            if languages:
                print("  Languages:")
                for lang, count in sorted(languages.items(), key=lambda x: -x[1]):
                    print(f"    {lang}: {count}")
        else:
            print(f"Compilation failed: {resp.get('message', 'Unknown error')}")
    else:
        # Default: run MCP server
        mcp_server = create_mcp_server(str(project_root))

        async def _serve() -> None:
            # The daemon handles auto-compilation through file watching
            await mcp_server.run_stdio_async()

        asyncio.run(_serve())
