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
│   ├── settings.py             # project + user config (YAML)
│   ├── compile.py              # compile pipeline (scan → parse → incremental → Engine)
│   ├── ast_parser.py           # tree-sitter AST parsing + line-based fallback
│   ├── raw_nodes.py            # CodeNode → raw_nodes builder for Engine.compile()
│   ├── fingerprint.py          # SHA-256 per-file change detection
│   ├── file_discovery.py       # gitignore-aware file discovery (pathspec)
│   ├── engine.py               # vectorless Engine wrapper
│   └── ask.py                  # query interface (streaming)
└── tests/
    ├── test_ast_parser.py      # AST parsing, raw_nodes, fingerprint tests
    ├── test_compile.py         # compile + language detection tests
    ├── test_file_discovery.py  # file discovery + gitignore tests
    ├── test_settings.py        # settings load/save tests
    └── test_basic.py           # import smoke test
```

### Upstream dependency

- `vectorless` — Rust document understanding engine (PyO3 bindings), provides `compile(raw_nodes=...)`, `ask()`, `query_stream()`, `NavigableDocument` API

### Compile pipeline

```
vcc compile
  │
  ├─ File Discovery (gitignore-aware, pathspec)
  │
  ├─ _scan_files() — single pass: read → hash + stats
  │
  ├─ Incremental detection (SHA-256 vs cached hashes)
  │   ├─ Changed/new → parse with tree-sitter AST
  │   └─ Unchanged → reuse cached raw_nodes
  │
  ├─ Engine.compile(raw_nodes=nodes, name="project")
  │   └─ Rust pipeline: BuildPass → EnrichPass → ReasoningPass → NavigationPass → ...
  │
  └─ Save hashes + parsed cache for next run
```

### AST parsing

- **tree-sitter** extracts semantic nodes per language (functions, classes, methods, etc.)
- `SPLITTABLE_NODE_TYPES` dict defines which AST node types to extract per language
- Parser is cached per language (`_parser_cache`) — reused across files
- **Fallback**: line-based splitting on blank-line boundaries when tree-sitter is unavailable
- Languages: Python, Rust, Go, JavaScript, TypeScript, Java, C, C++, Ruby, Swift, Kotlin, Scala

### Raw node tree structure

```
Level 1: src/main.py           (file, content = "Language: python")
Level 2: class_definition: App (content = full class source)
Level 3: function_definition: __init__  (content = method source)
Level 2: function_definition: main      (content = function source)
```

## Build Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# CLI testing
vcc init
vcc compile
vcc ask "query"
vcc status

# Run tests
python -m pytest tests/ -v

# Lint
ruff check src/ tests/ --fix
ruff format src/ tests/

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
- `logging` module for Python, `tracing` for Rust

## CLI Commands

| Command | Entry | Description |
|---------|-------|-------------|
| `vcc init` | `cli.init()` | Create `.vectorless_code/settings.yml` |
| `vcc compile` | `cli.compile()` | Compile codebase (AST parsing + incremental) |
| `vcc ask <q>` | `cli.ask()` | Ask a question about the codebase |
| `vcc status` | `cli.status()` | Show compilation status and stats |

## Settings Layout

```
project-root/
└── .vectorless_code/
    ├── settings.yml        # include/exclude patterns
    └── cache/
        ├── hashes.json     # per-file SHA-256 hashes (incremental)
        └── parsed_nodes.json  # cached raw_nodes per file
```

## Optional Dependencies

- tree-sitter + 12 language grammars are included as default dependencies
- If any grammar fails to install, the parser falls back to line-based splitting automatically

## ⚠️ Agent Behavior Constraints

Destructive operations require confirmation:
- File deletion (`rm`, `rm -rf`)
- Destructive git operations (`git push --force`, `git reset --hard`)
- Never commit sensitive files (`.env`, credentials, API keys)
- Never bypass pre-commit hooks (`--no-verify`)
