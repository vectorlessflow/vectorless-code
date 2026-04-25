"""Compile a codebase into a vectorless Document."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from vectorless_code.file_discovery import discover_files
from vectorless_code.settings import ProjectSettings, data_dir, load_project_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File marker used in the virtual document
# ---------------------------------------------------------------------------

_FILE_MARKER = "@@@FILE:{}@@@"


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
# Virtual document builder
# ---------------------------------------------------------------------------


def build_virtual_document(project_root: Path, files: list[Path]) -> str:
    """Concatenate code files into a single virtual document.

    Each file is prefixed with a ``@@@FILE:relative/path@@@`` marker.
    The Rust CodeParser uses these markers to split into per-file RawNodes.

    Files are sorted by relative path for deterministic output.
    """
    parts: list[str] = []
    for f in sorted(files):
        rel = f.relative_to(project_root).as_posix()
        try:
            content = f.read_text(errors="replace")
        except OSError as e:
            logger.warning("Skipping unreadable file %s: %s", rel, e)
            continue
        parts.append(f"{_FILE_MARKER.format(rel)}\n{content}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main compile function
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
    return sorted(discover_files(root, settings, include=include, exclude=exclude), key=lambda p: str(p))


def gather_stats(files: list[Path], project_root: Path) -> CompileResult:
    """Gather file statistics without compiling."""
    result = CompileResult(file_count=len(files))
    languages: dict[str, int] = {}

    for f in files:
        try:
            content = f.read_text(errors="replace")
            result.total_lines += content.count("\n") + 1
            result.total_bytes += len(content.encode("utf-8"))
            lang = detect_language(f)
            languages[lang] = languages.get(lang, 0) + 1
        except OSError:
            continue

    result.languages = languages
    return result


def compile_project(
    project_root: Path,
    settings: ProjectSettings | None = None,
) -> CompileResult:
    """Compile a codebase into a searchable index.

    Current implementation: discovers files, builds virtual document,
    gathers statistics. Vectorless engine integration coming next.

    Args:
        project_root: Project root directory (must be initialized).
        settings: Project settings. Loaded from disk if not provided.

    Returns:
        CompileResult with file stats and (future) doc_id.
    """
    root = project_root.resolve()

    if settings is None:
        settings = load_project_settings(root)

    # 1. Discover files
    files = collect_files(root, settings)
    if not files:
        return CompileResult(error="No code files found. Check your include_patterns in settings.")

    logger.info("Discovered %d files in %s", len(files), root)

    # 2. Build virtual document
    virtual_doc = build_virtual_document(root, files)
    logger.info("Virtual document: %d bytes", len(virtual_doc.encode("utf-8")))

    # 3. Gather stats
    result = gather_stats(files, root)

    # 4. TODO: Call vectorless engine to compile the virtual document
    # result.doc_id = await engine.compile(content=virtual_doc, format="code", name=root.name)

    logger.info(
        "Compile result: %d files, %d lines, %d bytes, languages=%s",
        result.file_count, result.total_lines, result.total_bytes, result.languages,
    )

    return result
