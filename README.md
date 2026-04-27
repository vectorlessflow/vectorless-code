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

| Command | Description |
|---------|-------------|
| `vcc init` | Initialize project (creates `.vectorless_code/settings.yml`) |
| `vcc compile` | Compile codebase into searchable index |
| `vcc ask <question>` | Ask a question about the codebase |
| `vcc status` | Show compilation status and index statistics |

`vcc` is the short name. `vectorless-code` also works.

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
