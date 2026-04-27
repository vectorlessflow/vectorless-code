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

# The daemon starts automatically and watches for file changes
# Use the CLI from your host (if vcc is installed) or inside the container:
docker compose exec vectorless-code vcc init
docker compose exec vectorless-code vcc status
```

## Simplified Architecture

The container runs the **daemon directly**:
- No entrypoint wrapper needed
- Daemon manages its own lifecycle
- File watching for auto-recompile
- Auto-restart on settings changes

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VCC_RUNTIME_DIR` | Daemon runtime directory (socket, PID, log) | `/var/vectorless_code` |
| `VCC_DAEMON_SUPERVISED` | Enable supervised mode | `1` |
| `VCC_HOST_PATH_MAPPING` | Map container paths to host paths | `/workspace=$HOME` |
| `PUID` | Host user ID (Linux only) | - |
| `PGID` | Host group ID (Linux only) | - |
| `VCC_HOST_WORKSPACE` | Path to mount as workspace | `$HOME` |

## Volumes

- `/workspace` — Your codebase (bind mount)
- `/var/vectorless_code` — Daemon runtime data (named volume)

## Usage Examples

```bash
# From inside the container
docker compose exec vectorless-code vcc status
docker compose exec vectorless-code vcc ask "how does authentication work?"

# From host (if vcc is installed and daemon is exposed)
vcc --daemon-addr /var/vectorless_code/daemon.sock status
```

## Notes

- The daemon runs as the `vcc` user (UID 1000)
- On Linux, use `PUID`/`PGID` to match your host user for file permissions
- Cache is stored in `.vectorless_code/` within your workspace
- The daemon automatically recompiles when source files change
- Restart policy: `unless-stopped` (use `docker compose stop` to stop)

## Pushing to Registry

```bash
# Tag for your registry
docker tag vcc:latest ztgx/vectorless-code:latest

# Push to Docker Hub
docker push ztgx/vectorless-code:latest

# With version tag
docker tag vcc:latest ztgx/vectorless-code:0.1.1
docker push ztgx/vectorless-code:0.1.1
```
