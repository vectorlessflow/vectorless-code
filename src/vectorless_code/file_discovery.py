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

    Yields absolute paths to matching files.

    Args:
        project_root: Project root directory.
        settings: Project settings (include/exclude patterns).
        include: Extra include patterns (merged with settings).
        exclude: Extra exclude patterns (merged with settings + gitignore).
    """
    root = project_root.resolve()

    # Build exclude spec: settings + gitignore + extra
    exclude_patterns = list(settings.exclude_patterns)
    gitignore_spec = _load_gitignore(root)
    if gitignore_spec is not None:
        # gitignore lines are already gitignore-style patterns
        pass
    if exclude:
        exclude_patterns.extend(exclude)

    exclude_spec = pathspec.PathSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, exclude_patterns
    )

    # Build include spec
    include_patterns = list(settings.include_patterns)
    if include:
        include_patterns.extend(include)
    include_spec = pathspec.PathSpec.from_lines(
        pathspec.patterns.GitWildMatchPattern, include_patterns
    )

    # Walk and filter
    for path in _walk_files(root):
        rel = path.relative_to(root)
        rel_str = rel.as_posix()

        # Must match include
        if not include_spec.match_file(rel_str):
            continue

        # Must not match exclude
        if exclude_spec.match_file(rel_str):
            continue

        # Must not match gitignore (separate check — gitignore semantics differ)
        if gitignore_spec is not None and gitignore_spec.match_file(rel_str):
            continue

        yield path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_gitignore(project_root: Path) -> pathspec.PathSpec | None:
    """Load ``.gitignore`` from project root if present."""
    gitignore = project_root / ".gitignore"
    if not gitignore.is_file():
        return None
    try:
        lines = gitignore.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines:
        return None
    return pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, lines)


def _walk_files(root: Path) -> Iterator[Path]:
    """Yield all regular files under *root*, skipping unreadable dirs."""
    try:
        entries = sorted(root.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry
