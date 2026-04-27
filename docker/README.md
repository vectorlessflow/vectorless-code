# vectorless-code Docker Usage

## Quick Start

```bash
# Build the image
docker build -t vcc:latest -f docker/Dockerfile .

# Run with Docker Compose (recommended)
# macOS / Windows
docker compose up -d

# Linux (aligns file ownership with your host user)
PUID=$(id -u) PGID=$(id -g) docker compose up -d

# Then use the CLI normally
docker compose exec vectorless-code vcc init
docker compose exec vectorless-code vcc compile
docker compose exec vectorless-code vcc ask "how does authentication work?"
```

## Architecture

The container runs a **supervised daemon** that:
- Keeps the container alive across daemon restarts
- Auto-restarts when settings change
- Gracefully handles `docker stop` via SIGTERM forwarding

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VECTORLESS_HOST_PATH_MAPPING` | Map container paths to host paths | `/workspace=$HOME` |
| `VECTORLESS_CODE_DIR` | Project config directory | `/workspace/.vectorless_code` |
| `VECTORLESS_CODE_RUNTIME_DIR` | Daemon runtime directory | `/var/vectorless_code` |
| `VECTORLESS_CODE_DAEMON_SUPERVISED` | Enable supervised mode | `1` |
| `PUID` | Host user ID (Linux only) | - |
| `PGID` | Host group ID (Linux only) | - |
| `VCC_HOST_WORKSPACE` | Path to mount as workspace | `$HOME` |

## Volumes

- `/workspace` — Your codebase (bind mount)
- `/var/vectorless_code` — Daemon runtime data (named volume)

## Daemon Management

```bash
# Check daemon status
docker compose exec vectorless-code vcc daemon status

# Restart daemon
docker compose exec vectorless-code vcc daemon restart

# Stop container (stops daemon)
docker compose down
```

## Notes

- The daemon runs as the `vcc` user (UID 1000)
- On Linux, use `PUID`/`PGID` to match your host user for file permissions
- Cache is stored in `.vectorless_code/` within your workspace
- The container auto-restarts the daemon on settings changes
