# vectorless-code

[![PyPI](https://img.shields.io/pypi/v/vectorless-code.svg)](https://pypi.org/project/vectorless-code/)
[![PyPI Downloads](https://static.pepy.tech/badge/vectorless-code/month)](https://pepy.tech/projects/vectorless-code)

Code-aware search and navigation engine, powered by [`vectorless`](https://github.com/vectorlessflow/vectorless).

## Install

```bash
pip install vectorless-code
```

## Quick start

```bash
# Initialize in your project
vcc init

# Compile the codebase
vcc compile

# Search
vcc ask "where is the authentication logic"
```

## Commands

### Core commands

| Command | Description |
|---------|-------------|
| `vcc init` | Initialize project (creates `.vectorless_code/settings.yml`) |
| `vcc compile` | Compile codebase into searchable index |
| `vcc ask <question>` | Ask a question about the codebase |
| `vcc status` | Show compilation status and index statistics |

### Daemon management

| Command | Description |
|---------|-------------|
| `vcc daemon status` | Show daemon status and loaded projects |
| `vcc daemon restart` | Restart the daemon |
| `vcc daemon stop` | Stop the daemon |

### Utilities

| Command | Description |
|---------|-------------|
| `vcc doctor` | Check system health and report issues |
| `vcc reset [--all]` | Reset project databases and optionally settings |
| `vcc mcp` | Run as MCP server (stdio mode) |

## MCP Server

vectorless-code includes a built-in MCP (Model Context Protocol) server for integration with AI assistants:

```bash
# Run MCP server manually
vcc mcp

# Or use as module
python -m vectorless_code
```

The MCP server provides an `ask` tool for semantic code search with:
- Natural language questions
- Configurable result limits
- Incremental index updates

## Environment Variables

| Variable | Description |
|----------|-------------|
| `VECTORLESS_API_KEY` | API key for vectorless service |
| `VECTORLESS_MODEL` | Model to use (e.g., `gpt-4o`) |
| `VECTORLESS_ENDPOINT` | API endpoint URL |
| `VECTORLESS_DAEMON_SUPERVISED` | Set to `1` when daemon is managed externally (e.g., Docker) |
| `VECTORLESS_HOST_CWD` | Host working directory (for containerized environments) |
| `VECTORLESS_HOST_PATH_MAPPING` | Path mappings for Docker (format: `/host:/container`) |

## Docker Support

vectorless-code supports containerized environments with automatic path mapping:

```bash
docker run -v /host/project:/project \
  -e VECTORLESS_HOST_PATH_MAPPING=/host/project:/project \
  vectorless-code
```

## Development

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended)

### Setup

```bash
# Clone
git clone https://github.com/vectorlessflow/vectorless-code.git
cd vectorless-code

# Install with dev dependencies
uv sync --all-extras

# Or install specific extra group
uv sync --extra dev
```

### Run tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test
python -m pytest tests/test_compile.py::TestDetectLanguage -v
```

### Lint and format

```bash
ruff check src/ tests/ --fix
ruff format src/ tests/
```

### Type check

```bash
mypy src/
```

## License

Apache-2.0
