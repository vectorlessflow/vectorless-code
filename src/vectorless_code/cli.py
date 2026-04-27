"""CLI entry point for vectorless-code (vcc command)."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import typer

from vectorless_code import __version__

logger = logging.getLogger(__name__)
from vectorless_code.daemon_client import DaemonStartError, is_daemon_running, start_daemon, stop_daemon
from vectorless_code.daemon.protocol import (
    DoctorCheckResult,
    IndexingProgress,
    ProjectStatusResponse,
    SearchResponse,
)
from vectorless_code.settings import (
    add_to_gitignore,
    data_dir,
    find_project_root,
    load_user_settings,
    normalize_path,
    remove_from_gitignore,
    save_initial_settings,
    save_user_settings,
    settings_path,
)

app = typer.Typer(
    name="vcc",
    help="vectorless-code — Code-aware search and navigation engine.",
    no_args_is_help=True,
)

daemon_app = typer.Typer(name="daemon", help="Manage the daemon process.")
app.add_typer(daemon_app, name="daemon")


@app.callback()
def _apply_host_cwd() -> None:
    """Honor VECTORLESS_HOST_CWD when forwarded from docker exec wrapper."""
    host_cwd = os.environ.get("VECTORLESS_HOST_CWD")
    if not host_cwd:
        return
    target = normalize_path(host_cwd)
    try:
        os.chdir(target)
    except OSError as e:
        typer.echo(
            f"Warning: VECTORLESS_HOST_CWD={host_cwd!r} → {target!r} "
            f"is not accessible: {e}. Continuing with cwd={os.getcwd()!r}.",
            err=True,
        )


# ---------------------------------------------------------------------------
# Shared CLI helpers
# ---------------------------------------------------------------------------


def require_project_root() -> Path:
    """Find the project root by walking up from CWD."""
    root = find_project_root(Path.cwd())
    if root is None:
        typer.echo(
            "Error: Not in an initialized project directory.\n"
            "Run `vcc init` in your project root to get started.",
            err=True,
        )
        raise typer.Exit(code=1)
    return root


def require_user_settings() -> tuple[Path, any]:
    """Load user settings, exiting with helpful message if not configured."""
    user_settings = load_user_settings()
    missing = []
    if not user_settings.api_key:
        missing.append("VECTORLESS_API_KEY")
    if not user_settings.model:
        missing.append("VECTORLESS_MODEL")
    if not user_settings.endpoint:
        missing.append("VECTORLESS_ENDPOINT")
    if missing:
        typer.echo(
            f"Error: {' and '.join(missing)} not set.\n"
            "Run `vcc init` to configure, or set the environment variables.",
            err=True,
        )
        raise typer.Exit(code=1)
    return settings_path(Path.home()), user_settings


_F = TypeVar("_F", bound=Callable[..., object])


def _catch_daemon_start_error(func: _F) -> _F:
    """Decorator that catches DaemonStartError and exits with a clean message."""

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> object:
        try:
            return func(*args, **kwargs)
        except DaemonStartError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=1)

    return wrapper  # type: ignore[return-value]


def _format_progress(progress: IndexingProgress) -> str:
    """Format an IndexingProgress snapshot as a human-readable string."""
    return (
        f"{progress.files_scanned} files scanned"
        f" | {progress.files_processed} processed"
        f" | {progress.files_unchanged} unchanged"
        f" | {progress.files_error} errors"
    )


def print_project_header(project_root: str) -> None:
    """Print the project root directory."""
    typer.echo(f"Project: {project_root}")


def print_index_stats(status: ProjectStatusResponse) -> None:
    """Print formatted index statistics."""
    if status.progress is not None:
        typer.echo(f"Indexing in progress: {_format_progress(status.progress)}")
    if not status.doc_id:
        typer.echo("\nNot compiled yet.")
        return
    typer.echo("\nIndex stats:")
    typer.echo(f"  Files:  {status.file_count}")
    typer.echo(f"  Lines:  {status.total_lines}")
    typer.echo(f"  Size:   {status.total_bytes} bytes")
    if status.languages:
        typer.echo("  Languages:")
        for lang, count in sorted(status.languages.items(), key=lambda x: -x[1]):
            typer.echo(f"    {lang}: {count}")


def print_search_results(response: SearchResponse) -> None:
    """Print formatted search results."""
    if not response.success:
        typer.echo(f"Search failed: {response.message}", err=True)
        return

    if not response.results:
        typer.echo("No results found.")
        return

    for i, r in enumerate(response.results, 1):
        typer.echo(f"\n--- Result {i} (score: {r.score:.3f}) ---")
        source = r.source_path or r.doc_name or "unknown"
        typer.echo(f"File: {source}")
        if r.node_title:
            typer.echo(f"  {r.node_title}")
        if r.content:
            preview = r.content.strip().splitlines()[:5]
            for line in preview:
                typer.echo(f"  {line}")

    if response.confidence > 0:
        typer.echo(f"\nConfidence: {response.confidence:.0%}")


def _run_index_with_progress(project_root: str) -> None:
    """Run indexing with streaming progress display. Exits on failure."""
    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner

    from vectorless_code.daemon_client import DaemonClient

    err_console = Console(stderr=True)
    last_progress_line: str | None = None

    with Live(Spinner("dots", "Indexing..."), console=err_console, transient=True) as live:

        async def _on_progress_async(progress_data: dict) -> None:
            nonlocal last_progress_line
            progress = IndexingProgress(**progress_data)
            last_progress_line = f"Indexing: {_format_progress(progress)}"
            live.update(Spinner("dots", last_progress_line))

        async def _on_waiting_async() -> None:
            live.update(
                Spinner(
                    "dots",
                    "Another indexing is ongoing, waiting for it to finish...",
                )
            )

        try:
            client = DaemonClient()
            resp = asyncio.run(client.index(project_root, on_progress=_on_progress_async, on_waiting=_on_waiting_async))
        except RuntimeError as e:
            live.stop()
            if isinstance(e, DaemonStartError):
                raise
            typer.echo(f"Indexing failed: {e}", err=True)
            raise typer.Exit(code=1)

    if last_progress_line is not None:
        typer.echo(last_progress_line, err=True)

    if not resp.get('success', False):
        typer.echo(f"Indexing failed: {resp.get('message', 'Unknown error')}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Files:  {resp.get('file_count', 0)}")
    typer.echo(f"Lines:  {resp.get('total_lines', 0)}")
    typer.echo(f"Size:   {resp.get('total_bytes', 0)} bytes")
    languages = resp.get('languages', {})
    if languages:
        typer.echo("Languages:")
        for lang, count in sorted(languages.items(), key=lambda x: -x[1]):
            typer.echo(f"  {lang}: {count}")


def _search_with_wait_spinner(
    project_root: str,
    query: str,
    doc_ids: list[str] | None = None,
    limit: int = 5,
    offset: int = 0,
) -> dict:
    """Run search, showing a spinner if waiting for load-time indexing."""
    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner

    from vectorless_code.daemon_client import DaemonClient

    err_console = Console(stderr=True)

    with Live(Spinner("dots", "Searching..."), console=err_console, transient=True) as live:

        async def _on_waiting_async() -> None:
            live.update(
                Spinner("dots", "Waiting for indexing to complete..."),
                refresh=True,
            )

        client = DaemonClient()
        resp = asyncio.run(client.search(
            project_root=project_root,
            query=query,
            doc_ids=doc_ids,
            limit=limit,
            offset=offset,
            on_waiting=_on_waiting_async,
        ))

    return resp


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize vectorless-code in the current project."""
    cwd = Path.cwd().resolve()
    sfile = settings_path(cwd)

    logger.info("Initializing vectorless-code in %s", cwd)

    if sfile.is_file():
        typer.echo("Project already initialized.")
        logger.debug("Project settings already exist at %s", sfile)
    else:
        path = save_initial_settings(cwd)
        typer.echo(f"Created project settings: {path}")
        add_to_gitignore(cwd)
        typer.echo("Run `vcc compile` to build the code index.")
        logger.info("Created project settings at %s", path)

    user_settings_file, user_settings = require_user_settings()

    if not user_settings.api_key:
        typer.echo("")
        api_key = typer.prompt("Enter your VECTORLESS_API_KEY", show_default=False)
        user_settings.api_key = api_key

    if not user_settings.model:
        typer.echo("")
        model = typer.prompt("Enter your VECTORLESS_MODEL")
        user_settings.model = model

    if not user_settings.endpoint:
        typer.echo("")
        endpoint = typer.prompt("Enter your VECTORLESS_ENDPOINT", show_default=False)
        user_settings.endpoint = endpoint

    saved = save_user_settings(user_settings)
    typer.echo(f"Settings saved to {saved}")


@app.command("compile")
@_catch_daemon_start_error
def compile_cmd() -> None:
    """Compile the codebase into a searchable index."""
    project_root = str(require_project_root())
    logger.info("Compiling project: %s", project_root)
    print_project_header(project_root)
    _run_index_with_progress(project_root)
    logger.info("Compilation complete for %s", project_root)


@app.command()
@_catch_daemon_start_error
def ask(
    question: list[str] = typer.Argument(..., help="Question about the codebase"),
) -> None:
    """Ask a question about the codebase."""
    project_root = str(require_project_root())
    query_str = " ".join(question)

    logger.info("Querying project %s: %s", project_root, query_str[:100])
    print_project_header(project_root)

    resp_dict = _search_with_wait_spinner(
        project_root=project_root,
        query=query_str,
        limit=10,
    )
    print_search_results(SearchResponse(**resp_dict))


@app.command()
@_catch_daemon_start_error
def status() -> None:
    """Show compilation status and index statistics."""
    from vectorless_code.daemon_client import DaemonClient

    project_root_path = require_project_root()
    project_root = str(project_root_path)
    print_project_header(project_root)

    typer.echo(f"Settings: {settings_path(project_root_path)}")

    try:
        client = DaemonClient()
        resp = asyncio.run(client.project_status(project_root))
        
        # Adapt dictionary response to ProjectStatusResponse or handle directly
        # Assuming the daemon returns a dict that can be unpacked or handled
        # If strict typing is needed, we might construct ProjectStatusResponse if fields match
        # For now, printing basic info based on typical status response
        
        if resp.get('indexed', False):
            typer.echo("Status: Indexed")
            typer.echo(f"Files:  {resp.get('file_count', 0)}")
            typer.echo(f"Nodes:  {resp.get('node_count', 0)}")
            typer.echo(f"Size:   {resp.get('total_bytes', 0)} bytes")
            if 'last_modified' in resp:
                typer.echo(f"Last modified: {resp['last_modified']}")
        else:
            typer.echo("Status: Not indexed")
            typer.echo("Run `vcc compile` to build the index.")

    except RuntimeError as e:
        if isinstance(e, DaemonStartError):
            raise
        typer.echo(f"Failed to get status: {e}", err=True)
        raise typer.Exit(code=1)


@app.command()
def reset(
    all_: bool = typer.Option(False, "--all", help="Also remove settings and .gitignore entry"),
    force: bool = typer.Option(False, "-f", "--force", help="Skip confirmation"),
) -> None:
    """Reset project databases and optionally remove settings."""
    project_root = require_project_root()
    vcc_dir = project_root / ".vectorless_code"

    cache_dir = vcc_dir / "cache"
    settings_file = settings_path(project_root)

    to_delete: list[Path] = []
    if cache_dir.exists():
        to_delete.append(cache_dir)
    if all_ and settings_file.exists():
        to_delete.append(settings_file)

    if not to_delete:
        typer.echo("Nothing to reset.")
        return

    typer.echo("The following will be deleted:")
    for p in to_delete:
        typer.echo(f"  {p}")

    if not force:
        if not typer.confirm("Proceed?"):
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

    # Remove project from daemon first
    try:
        from vectorless_code import client as _client

        _client.remove_project(str(project_root))
    except (ConnectionRefusedError, OSError, RuntimeError):
        pass

    import shutil

    for p in to_delete:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)

    if all_:
        if vcc_dir.exists() and not any(vcc_dir.iterdir()):
            try:
                vcc_dir.rmdir()
            except OSError:
                pass
        remove_from_gitignore(project_root)
        typer.echo("Project fully reset.")
    else:
        typer.echo("Cache deleted.")
        if settings_file.exists():
            typer.echo(
                "Settings file still exists. Run `vcc reset --all` to remove it too,\n"
                "or edit it manually."
            )


def _print_section(name: str) -> None:
    import click

    typer.echo()
    typer.echo(click.style(f"  {name}", bold=True))
    typer.echo(click.style(f"  {'─' * 38}", fg="bright_black"))


def _print_error(msg: str) -> None:
    import click

    typer.echo(click.style(f"  ERROR: {msg}", fg="red"), err=True)


def _print_doctor_result(result: DoctorCheckResult) -> None:
    import click

    if result.name == "done":
        return
    tag = _ok_fail_tag(result.ok)
    typer.echo(f"\n  {tag} {result.name}")
    for line in result.details:
        typer.echo(f"    {line}")
    for err in result.errors:
        typer.echo(click.style(f"    ERROR: {err}", fg="red"), err=True)


def _ok_fail_tag(ok: bool) -> str:
    import click

    if ok:
        return click.style("[OK]", fg="green", bold=True)
    return click.style("[FAIL]", fg="red", bold=True)


@app.command()
@_catch_daemon_start_error
def doctor() -> None:
    """Check system health and report issues."""
    from vectorless_code import client as _client
    from vectorless_code.settings import load_user_settings as _load_user_settings

    _print_section("User Settings")
    user_settings_path, _ = require_user_settings()
    typer.echo(f"  Settings: {user_settings_path}")

    _print_section("Daemon")
    daemon_ok = False
    try:
        from vectorless_code.daemon_client import DaemonClient
        client = DaemonClient()
        st_dict = asyncio.run(client.daemon_status())
        # Construct a simple object or access dict keys
        # Assuming st_dict has version, uptime_seconds, projects
        typer.echo(f"  Version: {st_dict.get('version', 'unknown')}")
        typer.echo(f"  Uptime: {st_dict.get('uptime_seconds', 0):.1f}s")
        projects = st_dict.get('projects', [])
        typer.echo(f"  Loaded projects: {len(projects)}")
        daemon_ok = True
    except Exception as e:
        _print_error(f"Cannot connect to daemon: {e}")

    if daemon_ok:
        try:
            client = DaemonClient()
            env_resp_dict = asyncio.run(client.daemon_env())
            path_mappings = env_resp_dict.get('path_mappings', [])
            if path_mappings:
                typer.echo("  Path mappings:")
                for m in path_mappings:
                    # m might be a dict or object depending on daemon protocol serialization
                    if isinstance(m, dict):
                        typer.echo(f"    {m.get('source')} → {m.get('target')}")
                    else:
                        typer.echo(f"    {m.source} → {m.target}")
        except Exception as e:
            _print_error(f"Failed to get daemon env: {e}")

    project_root = find_project_root(Path.cwd())

    if project_root is not None:
        _print_section("Project Settings")
        ps_path = settings_path(project_root)
        typer.echo(f"  Settings: {ps_path}")

        if daemon_ok:
            try:
                client = DaemonClient()
                
                async def _on_result_async(result_data: dict) -> None:
                    _print_doctor_result(DoctorCheckResult(**result_data))

                await client.doctor(
                    project_root=str(project_root),
                    on_result=_on_result_async,
                )
            except Exception as e:
                _print_error(f"Project checks failed: {e}")

    _print_section("Log Files")
    from vectorless_code.daemon_paths import daemon_log_path as _daemon_log_path

    typer.echo(f"  Daemon logs: {_daemon_log_path()}")


@app.command()
@_catch_daemon_start_error
def mcp() -> None:
    """Run as MCP server (stdio mode)."""
    import asyncio

    project_root = str(require_project_root())

    async def _run_mcp() -> None:
        from vectorless_code.server import create_mcp_server
        from vectorless_code.client import _bg_index

        asyncio.create_task(_bg_index(project_root))
        mcp_server = create_mcp_server(project_root)
        await mcp_server.run_stdio_async()

    asyncio.run(_run_mcp())


# ---------------------------------------------------------------------------
# Daemon subcommands
# ---------------------------------------------------------------------------


@daemon_app.command("status")
@_catch_daemon_start_error
def daemon_status() -> None:
    """Show daemon status."""
    from vectorless_code.daemon_client import DaemonClient

    client = DaemonClient()
    resp_dict = asyncio.run(client.daemon_status())
    
    typer.echo(f"Daemon version: {resp_dict.get('version', 'unknown')}")
    typer.echo(f"Uptime: {resp_dict.get('uptime_seconds', 0):.1f}s")
    projects = resp_dict.get('projects', [])
    if projects:
        typer.echo("Projects:")
        for p in projects:
            # p might be dict or object
            if isinstance(p, dict):
                root = p.get('project_root', 'unknown')
                indexing = p.get('indexing', False)
            else:
                root = p.project_root
                indexing = p.indexing
            state = "indexing" if indexing else "idle"
            typer.echo(f"  {root} [{state}]")
    else:
        typer.echo("No projects loaded.")


@daemon_app.command("restart")
@_catch_daemon_start_error
def daemon_restart() -> None:
    """Restart the daemon."""
    from vectorless_code.client import _wait_for_daemon

    typer.echo("Stopping daemon...")
    stop_daemon()

    typer.echo("Starting daemon...")
    proc = start_daemon()
    _wait_for_daemon(proc=proc)
    typer.echo("Daemon restarted.")


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the daemon."""
    from vectorless_code.daemon_paths import daemon_pid_path
    from vectorless_code.client import is_daemon_running

    pid_path = daemon_pid_path()
    if not pid_path.exists() and not is_daemon_running():
        typer.echo("Daemon is not running.")
        return

    stop_daemon()

    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not pid_path.exists() and not is_daemon_running():
            break
        time.sleep(0.1)

    if pid_path.exists() or is_daemon_running():
        typer.echo("Warning: daemon may not have stopped cleanly.", err=True)
    else:
        typer.echo("Daemon stopped.")


@app.command("run-daemon", hidden=True)
def run_daemon_cmd() -> None:
    """Internal: run the daemon process."""
    from vectorless_code.daemon import run_daemon

    run_daemon()


# Allow running as module: python -m vectorless_code.cli
if __name__ == "__main__":
    app()
