"""CLI entry point for vectorless-code (vcc command)."""

from __future__ import annotations

from pathlib import Path

import typer

from vectorless_code import __version__
from vectorless_code.settings import (
    add_to_gitignore,
    data_dir,
    find_project_root,
    load_project_settings,
    save_initial_settings,
    settings_path,
)

app = typer.Typer(
    name="vcc",
    help="vectorless-code — Code-aware search and navigation engine.",
    no_args_is_help=True,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"vectorless-code {__version__}")
        raise typer.Exit()


@app.callback()
def _global(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version.", callback=_version, is_eager=True
    ),
) -> None:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_project_root() -> Path:
    root = find_project_root(Path.cwd())
    if root is None:
        typer.echo(
            "Error: not in an initialized project directory.\n"
            "Run `vcc init` in your project root to get started.",
            err=True,
        )
        raise typer.Exit(code=1)
    return root


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize vectorless-code in the current project."""
    cwd = Path.cwd().resolve()
    sfile = settings_path(cwd)

    if sfile.is_file():
        typer.echo("Project already initialized.")
        return

    path = save_initial_settings(cwd)
    typer.echo(f"Created project settings: {path}")

    add_to_gitignore(cwd)

    typer.echo("Run `vcc compile` to build the code index.")


@app.command()
def compile() -> None:  # noqa: A001 — name matches user-facing verb
    """Compile the codebase into a searchable index."""
    project_root = _require_project_root()
    typer.echo(f"Project: {project_root}")
    typer.echo("Compiling codebase... (placeholder — not yet connected to vectorless)")
    typer.echo("Done.")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question about the codebase"),
) -> None:
    """Ask a question about the codebase."""
    project_root = _require_project_root()
    typer.echo(f"Project: {project_root}")
    typer.echo(f"Asking: {question}")
    typer.echo("(placeholder — not yet connected to vectorless)")


@app.command()
def status() -> None:
    """Show compilation status and index statistics."""
    project_root = _require_project_root()
    sfile = settings_path(project_root)
    ddir = data_dir(project_root)

    typer.echo(f"Project:  {project_root}")
    typer.echo(f"Settings: {sfile} [OK]")

    if not ddir.is_dir():
        typer.echo("\nNot compiled yet. Run `vcc compile` to build the index.")
        return

    index_files = [f for f in ddir.iterdir() if f.is_file()]
    if not index_files:
        typer.echo("\nIndex is empty. Run `vcc compile` to build the index.")
        return

    typer.echo(f"Index:    {ddir} ({len(index_files)} files)")

    # Placeholder stats — will be populated when connected to vectorless
    typer.echo("\nIndex stats:")
    typer.echo("  Compiled: (placeholder — not yet connected to vectorless)")
    typer.echo("  Files:    (placeholder)")
    typer.echo("  Symbols:  (placeholder)")


# Allow running as module: python -m vectorless_code.cli
if __name__ == "__main__":
    app()
