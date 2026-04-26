"""Settings schema, loading, saving, and path helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# User-level defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "gpt-4o"
_USER_SETTINGS_DIR = Path.home() / ".config" / "vectorless-code"
_USER_SETTINGS_FILE = _USER_SETTINGS_DIR / "settings.yaml"

# ---------------------------------------------------------------------------
# Project-level defaults
# ---------------------------------------------------------------------------

DEFAULT_INCLUDED_PATTERNS: list[str] = [
    "**/*.py",
    "**/*.pyi",
    "**/*.js",
    "**/*.jsx",
    "**/*.ts",
    "**/*.tsx",
    "**/*.mjs",
    "**/*.cjs",
    "**/*.rs",
    "**/*.go",
    "**/*.java",
    "**/*.c",
    "**/*.h",
    "**/*.cpp",
    "**/*.hpp",
    "**/*.cc",
    "**/*.cs",
    "**/*.rb",
    "**/*.php",
    "**/*.swift",
    "**/*.kt",
    "**/*.scala",
    "**/*.sql",
    "**/*.sh",
    "**/*.bash",
    "**/*.lua",
]

DEFAULT_EXCLUDED_PATTERNS: list[str] = [
    "**/.*",
    "**/__pycache__",
    "**/node_modules",
    "**/target",
    "**/build",
    "**/dist",
    "**/vendor",
    "**/.vectorless_code",
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProjectSettings:
    include_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDED_PATTERNS))
    exclude_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS))


@dataclass
class UserSettings:
    """User-level global configuration (~/.config/vectorless-code/settings.yaml)."""

    api_key: str | None = None
    model: str = _DEFAULT_MODEL
    endpoint: str | None = None

    @property
    def ready(self) -> bool:
        """Check if all required fields are configured."""
        return bool(self.api_key) and bool(self.model) and bool(self.endpoint)


def load_user_settings() -> UserSettings:
    """Load user settings from disk, falling back to environment variables."""
    api_key = os.environ.get("VECTORLESS_API_KEY")
    model = os.environ.get("VECTORLESS_MODEL", _DEFAULT_MODEL)
    endpoint = os.environ.get("VECTORLESS_ENDPOINT")

    if _USER_SETTINGS_FILE.is_file():
        try:
            with open(_USER_SETTINGS_FILE) as f:
                data = yaml.safe_load(f) or {}
            api_key = api_key or data.get("api_key")
            model = data.get("model", model)
            endpoint = endpoint or data.get("endpoint")
        except OSError:
            pass

    return UserSettings(api_key=api_key, model=model, endpoint=endpoint)


def save_user_settings(settings: UserSettings) -> Path:
    """Write user settings YAML. Returns path written."""
    _USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"model": settings.model}
    if settings.api_key:
        data["api_key"] = settings.api_key
    data["endpoint"] = settings.endpoint or ""
    with open(_USER_SETTINGS_FILE, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False)
    return _USER_SETTINGS_FILE


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_SETTINGS_DIR_NAME = ".vectorless_code"
_SETTINGS_FILE_NAME = "settings.yml"


def settings_dir(project_root: Path) -> Path:
    """Return ``$PROJECT_ROOT/.vectorless_code/``."""
    return project_root / _SETTINGS_DIR_NAME


def settings_path(project_root: Path) -> Path:
    """Return ``$PROJECT_ROOT/.vectorless_code/settings.yml``."""
    return project_root / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME


def data_dir(project_root: Path) -> Path:
    """Return ``$PROJECT_ROOT/.vectorless_code/data/``."""
    return project_root / _SETTINGS_DIR_NAME / "data"


def find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for ``.vectorless_code/settings.yml``.

    Returns the directory containing it, or ``None``.
    """
    current = start.resolve()
    home = Path.home().resolve()
    while True:
        if (current / _SETTINGS_DIR_NAME / _SETTINGS_FILE_NAME).is_file():
            return current
        if current == home:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _settings_to_dict(settings: ProjectSettings) -> dict[str, Any]:
    return {
        "include_patterns": settings.include_patterns,
        "exclude_patterns": settings.exclude_patterns,
    }


def _settings_from_dict(d: dict[str, Any]) -> ProjectSettings:
    return ProjectSettings(
        include_patterns=d.get("include_patterns", list(DEFAULT_INCLUDED_PATTERNS)),
        exclude_patterns=d.get("exclude_patterns", list(DEFAULT_EXCLUDED_PATTERNS)),
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_project_settings(project_root: Path) -> ProjectSettings:
    """Read ``$PROJECT_ROOT/.vectorless_code/settings.yml``.

    Raises ``FileNotFoundError`` if the file does not exist.
    """
    path = settings_path(project_root)
    if not path.is_file():
        raise FileNotFoundError(f"Project settings not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data:
        return ProjectSettings()
    return _settings_from_dict(data)


def save_project_settings(project_root: Path, settings: ProjectSettings) -> Path:
    """Write project settings YAML. Returns path written."""
    path = settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(_settings_to_dict(settings), f, default_flow_style=False)
    return path


def save_initial_settings(project_root: Path) -> Path:
    """Write the initial ``settings.yml`` with comment header.

    Used by ``vcc init``. Returns path written.
    """
    path = settings_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_INITIAL_SETTINGS_YAML)
    return path


_INITIAL_SETTINGS_YAML = f"""\
# vectorless-code project settings
# See https://vectorless.dev for documentation.

# File patterns to include in the index.
include_patterns:
{chr(10).join(f"  - {p!r}" for p in DEFAULT_INCLUDED_PATTERNS)}

# File patterns to exclude from the index.
exclude_patterns:
{chr(10).join(f"  - {p!r}" for p in DEFAULT_EXCLUDED_PATTERNS)}
"""


# ---------------------------------------------------------------------------
# Gitignore helper
# ---------------------------------------------------------------------------

_GITIGNORE_COMMENT = "# vectorless-code"
_GITIGNORE_ENTRY = "/.vectorless_code/"


def add_to_gitignore(project_root: Path) -> None:
    """Add ``/.vectorless_code/`` to ``.gitignore`` if ``.git`` exists."""
    if not (project_root / ".git").is_dir():
        return
    gitignore = project_root / ".gitignore"
    if gitignore.is_file():
        content = gitignore.read_text()
        if _GITIGNORE_ENTRY in content.splitlines():
            return
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"{_GITIGNORE_COMMENT}\n{_GITIGNORE_ENTRY}\n"
        gitignore.write_text(content)
    else:
        gitignore.write_text(f"{_GITIGNORE_COMMENT}\n{_GITIGNORE_ENTRY}\n")
