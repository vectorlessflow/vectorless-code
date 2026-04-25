"""CLI entry point for vectorless-code (vcc command)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from vectorless_code import __version__

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
# Settings helpers (lightweight — no heavy imports)
# ---------------------------------------------------------------------------

_SETTINGS_DIR_NAME = ".vectorless_code"
_SETTINGS_FILE_NAME = "settings.yml"


def _project_settings_path(root: Path) -> Path:
    return root / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME


def _find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for ``.vectorless_code/settings.yml``."""
    current = start.resolve()
    home = Path.home().resolve()
    while True:
        if (current / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME).is_file():
            return current
        if current == home:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _require_project_root() -> Path:
    root = _find_project_root(Path.cwd())
    if root is None:
        typer.echo(
            "Error: not in an initialized project directory.\n"
            "Run `vcc init` in your project root to get started.",
            err=True,
        )
        raise typer.Exit(code=1)
    return root


def _add_to_gitignore(project_root: Path) -> None:
    """Add ``/.vectorless_code/`` to ``.gitignore`` if ``.git`` exists."""
    if not (project_root / ".git").is_dir():
        return
    gitignore = project_root / ".gitignore"
    entry = "/.vectorless_code/"
    comment = "# vectorless-code"
    if gitignore.is_file():
        content = gitignore.read_text()
        if entry in content.splitlines():
            return
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"{comment}\n{entry}\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(f"{comment}\n{entry}\n")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialize vectorless-code in the current project."""
    cwd = Path.cwd().resolve()
    settings_file = _project_settings_path(cwd)

    if settings_file.is_file():
        typer.echo("Project already initialized.")
        return

    # Create .vectorless_code/settings.yml with defaults
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(_DEFAULT_SETTINGS_YAML)
    typer.echo(f"Created project settings: {settings_file}")

    _add_to_gitignore(cwd)

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
    settings_file = _project_settings_path(project_root)
    index_dir = project_root / _SETTINGS_DIR_NAME

    typer.echo(f"Project:  {project_root}")
    typer.echo(f"Settings: {settings_file}")

    # Check settings
    if settings_file.is_file():
        typer.echo(f"Settings: [OK]")
    else:
        typer.echo("Settings: [MISSING]")
        return

    # Check index data
    data_dir = index_dir / "data"
    if not data_dir.is_dir():
        typer.echo("\nNot compiled yet. Run `vcc compile` to build the index.")
        return

    # Count index files
    index_files = list(data_dir.iterdir()) if data_dir.exists() else []
    if not index_files:
        typer.echo("\nIndex is empty. Run `vcc compile` to build the index.")
        return

    typer.echo(f"Index:    {data_dir} ({len(index_files)} files)")

    # Placeholder stats — will be populated when connected to vectorless
    typer.echo("\nIndex stats:")
    typer.echo("  Compiled: (placeholder — not yet connected to vectorless)")
    typer.echo("  Files:    (placeholder)")
    typer.echo("  Symbols:  (placeholder)")


# ---------------------------------------------------------------------------
# Default settings YAML content
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS_YAML = """\
# vectorless-code project settings
# See https://vectorless.dev for documentation.

# File patterns to include in the index.
include_patterns:
  - "**/*.py"
  - "**/*.pyi"
  - "**/*.js"
  - "**/*.jsx"
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.rs"
  - "**/*.go"
  - "**/*.java"
  - "**/*.c"
  - "**/*.h"
  - "**/*.cpp"
  - "**/*.hpp"

# File patterns to exclude from the index.
exclude_patterns:
  - "**/.*"
  - "**/__pycache__"
  - "**/node_modules"
  - "**/target"
  - "**/build"
  - "**/dist"
  - "**/vendor"
  - "**/.vectorless_code"
"""

# Allow running as module: python -m vectorless_code.cli
if __name__ == "__main__":
    app()
