# CLAUDE.md

vectorless-code is a precise code search tool built on [vectorless](https://github.com/vectorlessflow/vectorless). No embeddings, no vector database — tree-sitter symbol indexing + LLM reasoning.

## Principles

- **Precision first.** Search results must resolve to exact symbols (functions, classes, methods). If it's not precise, it has no reason to exist.
- **Reason, don't vector.** Same philosophy as vectorless — retrieval is a reasoning act.

## Project Structure

Python project depending on the `vectorless` Rust core engine via PyO3:

```
vectorless-code/
├── pyproject.toml              # hatchling build, vcc / vectorless-code entry points
├── src/vectorless_code/
│   ├── __init__.py             # main() + __version__
│   ├── __main__.py             # python -m vectorless_code
│   ├── cli.py                  # CLI commands (typer)
│   ├── settings.py             # config management (planned)
│   ├── indexer.py              # code compilation (planned)
│   ├── search.py               # search strategies (planned)
│   ├── traversal.py            # LLM tree traversal (planned)
│   ├── server.py               # MCP server (planned)
│   └── file_discovery.py       # gitignore-aware file discovery (planned)
└── tests/
```

### Upstream dependency

- `vectorless` — Rust document understanding engine (PyO3 bindings), provides compile/ask/NavigableDocument API

## Build Commands

```bash
# Install (editable)
pip install -e .

# CLI testing
vcc init
vcc compile
vcc ask "query"
vcc status

# Lint
ruff check src/
ruff format src/

# Type check
mypy src/
```

## Code Conventions

- Python 3.11+, use modern syntax (`X | None`, `match`, etc.)
- CLI via `typer`, output via `rich`, errors to stderr
- Async code via `asyncio` (vectorless upstream is async)
- Settings in YAML (`pyyaml`), paths via `pathlib.Path`
- File discovery via `pathspec` (gitignore-compatible)
- `ruff` for formatting, line-length = 100
- `mypy --strict` for type checking
- Follow Rust standard naming for any Rust code (snake_case, PascalCase)
- Use `tracing` for logging in Rust, `logging` in Python

## CLI Commands

| Command | Entry | Description |
|---------|-------|-------------|
| `vcc init` | `cli.init()` | Create `.vectorless_code/settings.yml` |
| `vcc compile` | `cli.compile()` | Compile codebase into index (placeholder) |
| `vcc ask <q>` | `cli.ask()` | Ask a question about the codebase (placeholder) |
| `vcc status` | `cli.status()` | Show compilation status and stats |

## Settings Layout

```
project-root/
└── .vectorless_code/
    ├── settings.yml        # include/exclude patterns
    └── data/               # compiled artifacts (future)
```

## ⚠️ Agent Behavior Constraints

Destructive operations require confirmation:
- File deletion (`rm`, `rm -rf`)
- Destructive git operations (`git push --force`, `git reset --hard`)
- Never commit sensitive files (`.env`, credentials, API keys)
- Never bypass pre-commit hooks (`--no-verify`)
