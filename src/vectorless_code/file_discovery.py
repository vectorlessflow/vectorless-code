"""Gitignore-aware file discovery for code files."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pathspec

from vectorless_code.settings import ProjectSettings

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def discover_files(
    project_root: Path,
    settings: ProjectSettings,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Iterator[Path]:
    """Discover code files under *project_root*, respecting gitignore and settings.

    Walks the directory tree, loading ``.gitignore`` from every directory level
    (just like git does).  Files matched by any ``.gitignore`` are excluded.

    Yields absolute paths to matching files.

    Args:
        project_root: Project root directory.
        settings: Project settings (include/exclude patterns).
        include: Extra include patterns (merged with settings).
        exclude: Extra exclude patterns (merged with settings).
    """
    root = project_root.resolve()

    # Build include spec (settings + extra)
    include_patterns = list(settings.include_patterns)
    if include:
        include_patterns.extend(include)
    include_spec = pathspec.PathSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, include_patterns
    )

    # Build exclude spec (settings + extra)
    exclude_patterns = list(settings.exclude_patterns)
    if exclude:
        exclude_patterns.extend(exclude)
    exclude_spec = pathspec.PathSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, exclude_patterns
    )

    # Walk with hierarchical gitignore
    for path, gitignore_spec in _walk_with_gitignore(root, root):
        rel = path.relative_to(root)
        rel_str = rel.as_posix()

        if not include_spec.match_file(rel_str):
            continue

        if exclude_spec.match_file(rel_str):
            continue

        if gitignore_spec is not None and gitignore_spec.match_file(rel_str):
            continue

        yield path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_gitignore(directory: Path) -> pathspec.PathSpec | None:
    """Load ``.gitignore`` from a single directory."""
    gitignore = directory / ".gitignore"
    if not gitignore.is_file():
        return None
    try:
        lines = gitignore.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines:
        return None
    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, lines)


def _merge_specs(
    parent: pathspec.PathSpec | None, child: pathspec.PathSpec | None
) -> pathspec.PathSpec | None:
    """Merge parent and child gitignore specs.

    A file is ignored if it matches *either* the parent or the child spec.
    """
    if parent is None and child is None:
        return None
    patterns: list[str] = []
    if parent is not None:
        patterns.extend(p.pattern for p in parent.patterns)
    if child is not None:
        patterns.extend(p.pattern for p in child.patterns)
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, patterns)


def _walk_with_gitignore(
    root: Path,
    current: Path,
    parent_spec: pathspec.PathSpec | None = None,
) -> Iterator[tuple[Path, pathspec.PathSpec | None]]:
    """Walk directory tree, yielding ``(file_path, effective_gitignore_spec)``.

    At each directory level:
    1. Merge parent gitignore with local ``.gitignore``
    2. Skip the entire directory if it's matched by the effective spec
    3. Recurse into subdirectories
    4. Yield files with the effective spec for per-file checking
    """
    rel = current.relative_to(root)
    rel_str = rel.as_posix() if str(rel) != "." else ""

    # Load local .gitignore and merge with parent
    local_spec = _load_gitignore(current)
    effective_spec = _merge_specs(parent_spec, local_spec)

    # If this directory itself is ignored, skip entirely
    if effective_spec is not None and rel_str and effective_spec.match_file(rel_str):
        return

    try:
        entries = sorted(current.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.is_dir():
            yield from _walk_with_gitignore(root, entry, effective_spec)
        elif entry.is_file():
            yield entry, effective_spec
