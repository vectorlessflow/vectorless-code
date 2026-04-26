"""Per-file change detection for incremental compilation.

Computes SHA-256 hashes for each file and persists them to
``.vectorless_code/cache/hashes.json``. On subsequent compiles,
only changed/new files are re-parsed.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_HASHES_FILE = "hashes.json"


def file_hash(content: str | bytes) -> str:
    """SHA-256 hash of file content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def project_fingerprint(file_hashes: dict[str, str]) -> str:
    """Deterministic project-level fingerprint from all file hashes."""
    sorted_entries = sorted(file_hashes.items())
    combined = json.dumps(sorted_entries)
    return hashlib.sha256(combined.encode()).hexdigest()


def _cache_dir(project_root: Path) -> Path:
    return project_root / ".vectorless_code" / "cache"


def _hashes_path(project_root: Path) -> Path:
    return _cache_dir(project_root) / _HASHES_FILE


def load_hashes(project_root: Path) -> dict[str, str]:
    """Load previously stored file hashes. Returns empty dict if none exist."""
    path = _hashes_path(project_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_hashes(project_root: Path, hashes: dict[str, str]) -> Path:
    """Persist file hashes. Returns path written."""
    cache = _cache_dir(project_root)
    cache.mkdir(parents=True, exist_ok=True)
    path = _hashes_path(project_root)
    path.write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")
    return path


def detect_changes(
    current_hashes: dict[str, str],
    prev_hashes: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """Compare current vs previous hashes.

    Returns:
        (changed_or_new, unchanged, removed) file path lists.
    """
    changed_or_new: list[str] = []
    unchanged: list[str] = []
    removed: list[str] = []

    for path, h in current_hashes.items():
        if prev_hashes.get(path) != h:
            changed_or_new.append(path)
        else:
            unchanged.append(path)

    for path in prev_hashes:
        if path not in current_hashes:
            removed.append(path)

    return changed_or_new, unchanged, removed
