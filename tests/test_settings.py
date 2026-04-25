"""Tests for vectorless_code.settings."""

from pathlib import Path

import pytest

from vectorless_code.settings import (
    DEFAULT_EXCLUDED_PATTERNS,
    DEFAULT_INCLUDED_PATTERNS,
    ProjectSettings,
    add_to_gitignore,
    data_dir,
    find_project_root,
    load_project_settings,
    save_initial_settings,
    save_project_settings,
    settings_dir,
    settings_path,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_settings_dir(self, tmp_path: Path) -> None:
        assert settings_dir(tmp_path) == tmp_path / ".vectorless_code"

    def test_settings_path(self, tmp_path: Path) -> None:
        assert settings_path(tmp_path) == tmp_path / ".vectorless_code" / "settings.yml"

    def test_data_dir(self, tmp_path: Path) -> None:
        assert data_dir(tmp_path) == tmp_path / ".vectorless_code" / "data"


class TestFindProjectRoot:
    def test_finds_project(self, tmp_path: Path) -> None:
        settings_path(tmp_path).parent.mkdir(parents=True)
        settings_path(tmp_path).touch()
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        assert find_project_root(sub) == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        assert find_project_root(tmp_path) is None

    def test_stops_at_home(self, tmp_path: Path) -> None:
        # home dir should not be treated as a project root
        assert find_project_root(Path.home()) is None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


class TestLoadProjectSettings:
    def test_loads_defaults(self, tmp_path: Path) -> None:
        save_initial_settings(tmp_path)
        s = load_project_settings(tmp_path)
        assert s.include_patterns == DEFAULT_INCLUDED_PATTERNS
        assert s.exclude_patterns == DEFAULT_EXCLUDED_PATTERNS

    def test_raises_when_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_project_settings(tmp_path)


class TestSaveProjectSettings:
    def test_roundtrip(self, tmp_path: Path) -> None:
        original = ProjectSettings(
            include_patterns=["**/*.py"],
            exclude_patterns=["**/.*"],
        )
        save_project_settings(tmp_path, original)
        loaded = load_project_settings(tmp_path)
        assert loaded.include_patterns == ["**/*.py"]
        assert loaded.exclude_patterns == ["**/.*"]

    def test_creates_directory(self, tmp_path: Path) -> None:
        save_project_settings(tmp_path, ProjectSettings())
        assert settings_path(tmp_path).is_file()


class TestSaveInitialSettings:
    def test_creates_file(self, tmp_path: Path) -> None:
        path = save_initial_settings(tmp_path)
        assert path.is_file()
        assert path.parent == settings_dir(tmp_path)

    def test_file_is_valid_yaml(self, tmp_path: Path) -> None:
        save_initial_settings(tmp_path)
        s = load_project_settings(tmp_path)
        assert len(s.include_patterns) > 0
        assert len(s.exclude_patterns) > 0


# ---------------------------------------------------------------------------
# Gitignore
# ---------------------------------------------------------------------------


class TestAddToGitignore:
    def test_creates_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        add_to_gitignore(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert "/.vectorless_code/" in content

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / ".gitignore").write_text("*.pyc\n")
        add_to_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert "*.pyc" in content
        assert "/.vectorless_code/" in content

    def test_no_duplicate_entry(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        add_to_gitignore(tmp_path)
        add_to_gitignore(tmp_path)
        content = (tmp_path / ".gitignore").read_text()
        assert content.count("/.vectorless_code/") == 1

    def test_skips_when_no_git(self, tmp_path: Path) -> None:
        add_to_gitignore(tmp_path)
        assert not (tmp_path / ".gitignore").exists()
