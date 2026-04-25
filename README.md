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

## License

Apache-2.0
