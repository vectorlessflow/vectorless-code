"""Compile a codebase into a vectorless Document."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from vectorless_code.engine import create_engine
from vectorless_code.file_discovery import discover_files
from vectorless_code.settings import ProjectSettings, UserSettings, load_project_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compile result
# ---------------------------------------------------------------------------


@dataclass
class CompileResult:
    """Result of compiling a codebase."""

    file_count: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    doc_id: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXTENSION_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".lua": "lua",
}


def detect_language(path: Path) -> str:
    """Detect programming language from file extension."""
    return _EXTENSION_TO_LANG.get(path.suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def collect_files(
    project_root: Path,
    settings: ProjectSettings | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Path]:
    """Discover and return code files for a project.

    Returns absolute paths, sorted by relative path.
    """
    if settings is None:
        settings = load_project_settings(project_root)
    root = project_root.resolve()
    return sorted(discover_files(root, settings, include=include, exclude=exclude), key=str)


# ---------------------------------------------------------------------------
# Single-pass file scan: read + hash + stats (no parsing)
# ---------------------------------------------------------------------------


def _scan_files(
    files: list[Path],
    project_root: Path,
) -> tuple[dict[str, str], CompileResult, dict[str, str]]:
    """Single pass over files: read content, compute hash, gather stats.

    Does NOT parse files — parsing is deferred to the incremental step.

    Returns:
        (current_hashes, stats_result, content_map)
        - current_hashes: {rel_path: sha256_hex}
        - stats_result: CompileResult with file/line/byte/language stats
        - content_map: {rel_path: file_content_str}
    """
    stats = CompileResult(file_count=len(files))
    languages: dict[str, int] = {}
    current_hashes: dict[str, str] = {}
    content_map: dict[str, str] = {}

    for f in files:
        rel = f.relative_to(project_root).as_posix()
        lang = detect_language(f)

        try:
            content = f.read_text(errors="replace")
        except OSError as e:
            logger.warning("Skipping unreadable file %s: %s", rel, e)
            continue

        # Stats
        stats.total_lines += content.count("\n") + 1
        try:
            stats.total_bytes += f.stat().st_size
        except OSError:
            stats.total_bytes += len(content)
        languages[lang] = languages.get(lang, 0) + 1

        # Hash
        current_hashes[rel] = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Keep content for parsing later
        content_map[rel] = content

    stats.languages = languages
    return current_hashes, stats, content_map


# ---------------------------------------------------------------------------
# Incremental: load/save cached raw_nodes per file
# ---------------------------------------------------------------------------


def _cache_dir(project_root: Path) -> Path:
    return project_root / ".vectorless_code" / "cache"


def _nodes_cache_path(project_root: Path) -> Path:
    return _cache_dir(project_root) / "parsed_nodes.json"


def _hashes_path(project_root: Path) -> Path:
    return _cache_dir(project_root) / "hashes.json"


def _load_parsed_cache(project_root: Path) -> dict[str, list]:
    """Load previously cached raw_nodes per file."""
    path = _nodes_cache_path(project_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_parsed_cache(project_root: Path, per_file_raw: dict[str, list[dict]]) -> None:
    """Persist raw_nodes cache per file."""
    cache = _cache_dir(project_root)
    cache.mkdir(parents=True, exist_ok=True)
    path = _nodes_cache_path(project_root)
    path.write_text(json.dumps(per_file_raw, ensure_ascii=False), encoding="utf-8")


def _load_hashes(project_root: Path) -> dict[str, str]:
    path = _hashes_path(project_root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_hashes(project_root: Path, hashes: dict[str, str]) -> None:
    cache = _cache_dir(project_root)
    cache.mkdir(parents=True, exist_ok=True)
    path = _hashes_path(project_root)
    path.write_text(json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Parse only changed files, merge with cached
# ---------------------------------------------------------------------------


def _build_raw_nodes_incremental(
    current_hashes: dict[str, str],
    prev_hashes: dict[str, str],
    content_map: dict[str, str],
    cached_raw_nodes: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Parse changed files, reuse cached raw_nodes for unchanged files.

    Returns per-file raw_nodes dict.
    """
    from vectorless_code.ast_parser import parse_file
    from vectorless_code.raw_nodes import build_raw_nodes

    per_file_raw: dict[str, list[dict]] = {}

    for rel, content in content_map.items():
        if current_hashes.get(rel) != prev_hashes.get(rel):
            # Changed or new file — parse and build raw_nodes
            lang = detect_language(Path(rel))
            nodes = parse_file(rel, content, lang)
            per_file_raw[rel] = build_raw_nodes([(rel, lang, nodes)])
        else:
            # Unchanged — use cached
            if rel in cached_raw_nodes:
                per_file_raw[rel] = cached_raw_nodes[rel]
            else:
                # Cache miss — parse anyway
                lang = detect_language(Path(rel))
                nodes = parse_file(rel, content, lang)
                per_file_raw[rel] = build_raw_nodes([(rel, lang, nodes)])

    return per_file_raw


def _build_raw_nodes_full(content_map: dict[str, str]) -> dict[str, list[dict]]:
    """Parse all files (first compile)."""
    from vectorless_code.ast_parser import parse_file
    from vectorless_code.raw_nodes import build_raw_nodes

    per_file_raw: dict[str, list[dict]] = {}

    for rel in sorted(content_map.keys()):
        content = content_map[rel]
        lang = detect_language(Path(rel))
        nodes = parse_file(rel, content, lang)
        per_file_raw[rel] = build_raw_nodes([(rel, lang, nodes)])

    return per_file_raw


def _flatten_raw_nodes(per_file_raw: dict[str, list[dict]]) -> list[dict]:
    """Merge per-file raw_nodes into a single flat list, sorted by path."""
    raw_nodes: list[dict] = []
    for rel in sorted(per_file_raw.keys()):
        raw_nodes.extend(per_file_raw[rel])
    return raw_nodes


# ---------------------------------------------------------------------------
# Main compile function
# ---------------------------------------------------------------------------


async def compile_project(
    project_root: Path,
    settings: ProjectSettings | None = None,
    user_settings: UserSettings | None = None,
) -> CompileResult:
    """Compile a codebase into a searchable vectorless Document.

    Steps:
    1. Discover code files (gitignore-aware)
    2. Single pass: read + hash + stats (no parsing)
    3. Incremental: parse only changed files, reuse cached for unchanged
    4. Call Engine.compile(raw_nodes=...) to produce a Document

    Args:
        project_root: Project root directory (must be initialized).
        settings: Project settings. Loaded from disk if not provided.
        user_settings: User settings (API key, model). Loaded if not provided.

    Returns:
        CompileResult with file stats and doc_id on success.
    """
    root = project_root.resolve()

    if settings is None:
        settings = load_project_settings(root)

    # 1. Discover files
    files = collect_files(root, settings)
    if not files:
        return CompileResult(error="No code files found. Check your include_patterns in settings.")

    logger.info("Discovered %d files in %s", len(files), root)

    # 2. Load previous state for incremental
    prev_hashes = _load_hashes(root)
    cached_raw = _load_parsed_cache(root)
    has_cache = bool(prev_hashes) and bool(cached_raw)

    # 3. Single pass: read, hash, stats (no parsing)
    current_hashes, result, content_map = _scan_files(files, root)

    # 4. Detect changes
    changed = [p for p, h in current_hashes.items() if prev_hashes.get(p) != h]
    removed = [p for p in prev_hashes if p not in current_hashes]
    unchanged = len(current_hashes) - len(changed)

    logger.info(
        "Change detection: %d changed/new, %d unchanged, %d removed",
        len(changed),
        unchanged,
        len(removed),
    )

    # 5. Build raw_nodes: parse only changed files
    if has_cache:
        per_file_raw = _build_raw_nodes_incremental(
            current_hashes, prev_hashes, content_map, cached_raw
        )
        logger.info(
            "Incremental build: parsed %d changed, reused %d cached",
            len(changed),
            unchanged,
        )
    else:
        per_file_raw = _build_raw_nodes_full(content_map)
        logger.info("Full build: parsed %d files", len(per_file_raw))

    # Free content_map — no longer needed after parsing
    del content_map

    raw_nodes = _flatten_raw_nodes(per_file_raw)

    if not raw_nodes:
        return CompileResult(error="No parseable content found in discovered files.")

    logger.info("Total raw_nodes: %d", len(raw_nodes))

    # 6. Compile via vectorless Engine
    try:
        engine = create_engine(user_settings)
    except RuntimeError as e:
        result.error = str(e)
        return result

    async with engine:
        try:
            project_name = root.name
            compile_output = await engine.compile(
                raw_nodes=raw_nodes,
                name=project_name,
            )
            result.doc_id = compile_output.doc_id
            logger.info("Compiled: doc_id=%s", result.doc_id)
        except Exception as e:
            logger.error("Compile failed: %s", e)
            result.error = f"Compile failed: {e}"

    # 7. Save state for next incremental compile
    _save_hashes(root, current_hashes)
    _save_parsed_cache(root, per_file_raw)

    return result
