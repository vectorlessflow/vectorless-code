# vectorless-code

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

| Command | Description |
|---------|-------------|
| `vcc init` | Initialize project (creates `.vectorless_code/settings.yml`) |
| `vcc compile` | Compile codebase into searchable index |
| `vcc ask <question>` | Ask a question about the codebase |
| `vcc status` | Show compilation status and index statistics |

`vcc` is the short name. `vectorless-code` also works.


## Status

Early development. The CLI skeleton (`init`, `compile`, `ask`, `status`) is in place. Core search engine coming soon.

## Development

### Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

```bash
# Clone
git clone https://github.com/vectorlessflow/vectorless-code.git
cd vectorless-code

# Install with dev dependencies (editable mode)
pip install -e ".[dev]"
```

### Project layout

```
src/vectorless_code/       # Source code
tests/                     # Test files (pytest)
pyproject.toml             # Project config, dependencies, entry points
```

### Run tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_settings.py -v

# Run a specific test
python -m pytest tests/test_compile.py::TestDetectLanguage -v
```

### Lint and format

```bash
# Check
ruff check src/ tests/

# Auto-fix
ruff check src/ tests/ --fix

# Format
ruff format src/ tests/
```

### Type check

```bash
mypy src/
```

### Test the CLI

```bash
# In a temp directory
cd $(mktemp -d)
vcc init
vcc compile
vcc status
vcc ask "where is main"
```

## License

Apache-2.0
