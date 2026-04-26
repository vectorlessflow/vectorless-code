"""CLI entry point for vectorless-code (vcc command)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from vectorless_code import __version__
from vectorless_code.settings import (
    UserSettings,
    add_to_gitignore,
    data_dir,
    find_project_root,
    load_user_settings,
    save_initial_settings,
    save_user_settings,
    settings_path,
)

_DEFAULT_MODEL = "gpt-4o"

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


def _require_api_key(user_settings: UserSettings | None = None) -> UserSettings:
    settings = user_settings or load_user_settings()
    missing = []
    if not settings.api_key:
        missing.append("VECTORLESS_API_KEY")
    if not settings.model:
        missing.append("VECTORLESS_MODEL")
    if not settings.endpoint:
        missing.append("VECTORLESS_ENDPOINT")
    if missing:
        typer.echo(
            f"Error: {' and '.join(missing)} not set.\n"
            "Run `vcc init` to configure, or set the environment variables.",
            err=True,
        )
        raise typer.Exit(code=1)
    return settings


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
    else:
        path = save_initial_settings(cwd)
        typer.echo(f"Created project settings: {path}")
        add_to_gitignore(cwd)
        typer.echo("Run `vcc compile` to build the code index.")

    # Check / prompt for required settings
    user_settings = load_user_settings()

    if not user_settings.api_key:
        typer.echo("")
        api_key = typer.prompt("Enter your VECTORLESS_API_KEY", default="", show_default=False)
        if api_key:
            user_settings.api_key = api_key

    if not user_settings.model or user_settings.model == _DEFAULT_MODEL:
        typer.echo("")
        model = typer.prompt("Enter your VECTORLESS_MODEL", default=_DEFAULT_MODEL)
        if model:
            user_settings.model = model

    if not user_settings.endpoint:
        typer.echo("")
        endpoint = typer.prompt("Enter your VECTORLESS_ENDPOINT", default="", show_default=False)
        if endpoint:
            user_settings.endpoint = endpoint

    if user_settings.api_key or user_settings.endpoint:
        saved = save_user_settings(user_settings)
        typer.echo(f"Settings saved to {saved}")


@app.command()
def compile() -> None:  # noqa: A001 — name matches user-facing verb
    """Compile the codebase into a searchable index."""
    from vectorless_code.compile import compile_project

    project_root = _require_project_root()
    user_settings = _require_api_key()

    typer.echo(f"Project: {project_root}")

    result = asyncio.run(compile_project(project_root, user_settings=user_settings))

    if not result.ok:
        typer.echo(f"Error: {result.error}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Files:  {result.file_count}")
    typer.echo(f"Lines:  {result.total_lines}")
    typer.echo(f"Size:   {result.total_bytes} bytes")
    if result.doc_id:
        typer.echo(f"Doc ID: {result.doc_id}")
    if result.languages:
        typer.echo("Languages:")
        for lang, count in sorted(result.languages.items(), key=lambda x: -x[1]):
            typer.echo(f"  {lang}: {count} files")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question about the codebase"),
) -> None:
    """Ask a question about the codebase."""
    from vectorless_code.ask import ask_codebase, format_output
    from vectorless_code.engine import create_engine

    project_root = _require_project_root()
    user_settings = _require_api_key()

    typer.echo(f"Project: {project_root}")

    # Get doc_ids for this project
    doc_ids: list[str] | None = None
    try:
        engine = create_engine(user_settings)
        async def _get_doc_ids() -> list[str]:
            async with engine:
                docs = await engine.list_documents()
            return [d["doc_id"] for d in docs if isinstance(d, dict)] if docs else []

        all_doc_ids = asyncio.run(_get_doc_ids())
        if all_doc_ids:
            doc_ids = all_doc_ids
    except Exception:
        pass  # Will use None = query all

    def on_progress(msg: str) -> None:
        if msg:
            typer.echo(f"  {msg}", nl=False)

    output = asyncio.run(
        ask_codebase(question, doc_ids=doc_ids, user_settings=user_settings, on_progress=on_progress)
    )

    typer.echo("")
    typer.echo(format_output(output))


@app.command()
def status() -> None:
    """Show compilation status and index statistics."""
    project_root = _require_project_root()
    sfile = settings_path(project_root)
    ddir = data_dir(project_root)

    typer.echo(f"Project:  {project_root}")
    typer.echo(f"Settings: {sfile} [OK]")

    from vectorless_code.engine import create_engine

    try:
        user_settings = load_user_settings()
        engine = create_engine(user_settings)

        async def _status() -> list[object]:
            async with engine:
                return await engine.list_documents()

        docs = asyncio.run(_status())
        if docs:
            typer.echo(f"\nCompiled documents ({len(docs)}):")
            for doc in docs:
                if isinstance(doc, dict):
                    typer.echo(f"  {doc.get('doc_id', '?')} — {doc.get('name', '?')}")
                else:
                    typer.echo(f"  {doc}")
        else:
            typer.echo("\nNot compiled yet. Run `vcc compile` to build the index.")
    except RuntimeError:
        typer.echo("\nNo API key configured. Run `vcc init` to set up.")
    except Exception as e:
        typer.echo(f"\nCould not reach vectorless: {e}", err=True)


# Allow running as module: python -m vectorless_code.cli
if __name__ == "__main__":
    app()
