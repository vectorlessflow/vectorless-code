"""Tests for vectorless_code.file_discovery."""

from pathlib import Path

import pytest

from vectorless_code.file_discovery import discover_files
from vectorless_code.settings import ProjectSettings, save_initial_settings


def _make_project(tmp_path: Path, files: dict[str, str]) -> None:
    """Create a project tree. *files* maps ``"relative/path"`` → content."""
    save_initial_settings(tmp_path)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def _discovered_names(tmp_path: Path, settings: ProjectSettings, **kwargs) -> list[str]:
    """Return sorted relative paths of discovered files."""
    return sorted(
        str(f.relative_to(tmp_path)) for f in discover_files(tmp_path, settings, **kwargs)
    )


# ---------------------------------------------------------------------------
# Basic discovery
# ---------------------------------------------------------------------------


class TestBasicDiscovery:
    def test_finds_code_files(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "def main(): pass",
            "src/lib.rs": "fn main() {}",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert "src/main.py" in result
        assert "src/lib.rs" in result

    def test_ignores_non_code_files(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "README.md": "# hello",
            "image.png": "bytes",
            "src/main.py": "pass",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.py"]

    def test_empty_project(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {})
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == []


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


class TestExcludePatterns:
    def test_excludes_hidden_dirs(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            ".hidden/secret.py": "pass",
            "src/main.py": "pass",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.py"]

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "node_modules/pkg/index.js": "export {}",
            "src/app.ts": "export {}",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/app.ts"]

    def test_excludes_target(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "target/debug/main": "binary",
            "src/main.rs": "fn main() {}",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.rs"]


# ---------------------------------------------------------------------------
# Include patterns
# ---------------------------------------------------------------------------


class TestIncludePatterns:
    def test_only_includes_matching_patterns(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
            "src/utils.py": "pass",
            "data/config.toml": "[db]",
        })
        settings = ProjectSettings(include_patterns=["**/*.py"])
        result = _discovered_names(tmp_path, settings)
        assert len(result) == 2
        assert "src/main.py" in result

    def test_extra_include_merged(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
            "config.toml": "[db]",
        })
        settings = ProjectSettings(include_patterns=["**/*.py"])
        result = _discovered_names(tmp_path, settings, include=["**/*.toml"])
        assert "src/main.py" in result
        assert "config.toml" in result


# ---------------------------------------------------------------------------
# Gitignore integration
# ---------------------------------------------------------------------------


class TestGitignore:
    def test_respects_gitignore(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
            "src/generated.py": "# auto-generated",
        })
        (tmp_path / ".gitignore").write_text("src/generated.py\n")
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.py"]

    def test_gitignore_wildcard(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
            "src/test_mock.py": "pass",
            "src/test_foo.py": "pass",
        })
        (tmp_path / ".gitignore").write_text("src/test_*.py\n")
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.py"]

    def test_no_gitignore_fine(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
        })
        # no .gitignore file
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert result == ["src/main.py"]


# ---------------------------------------------------------------------------
# Multi-language
# ---------------------------------------------------------------------------


class TestMultiLanguage:
    def test_all_supported_extensions(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "a.py": "# py",
            "b.js": "// js",
            "c.ts": "// ts",
            "d.rs": "// rs",
            "e.go": "// go",
            "f.java": "// java",
            "g.cpp": "// cpp",
            "h.c": "// c",
        })
        settings = ProjectSettings()
        result = _discovered_names(tmp_path, settings)
        assert len(result) == 8
