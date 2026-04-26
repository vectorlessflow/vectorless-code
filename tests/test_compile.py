"""Tests for vectorless_code.compile."""

from pathlib import Path

import pytest

from vectorless_code.compile import (
    CompileResult,
    collect_files,
    detect_language,
)
from vectorless_code.settings import ProjectSettings, save_initial_settings


def _make_project(tmp_path: Path, files: dict[str, str]) -> None:
    """Create a project tree and initialize settings."""
    save_initial_settings(tmp_path)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("main.py", "python"),
            ("app.ts", "typescript"),
            ("app.tsx", "typescript"),
            ("index.js", "javascript"),
            ("main.rs", "rust"),
            ("main.go", "go"),
            ("App.java", "java"),
            ("main.c", "c"),
            ("main.cpp", "cpp"),
            ("main.h", "c"),
            ("main.hpp", "cpp"),
            ("Gemfile.lock", "unknown"),
        ],
    )
    def test_detects_language(self, filename: str, expected: str) -> None:
        assert detect_language(Path(filename)) == expected


# ---------------------------------------------------------------------------
# collect_files
# ---------------------------------------------------------------------------


class TestCollectFiles:
    def test_returns_matching_files(self, tmp_path: Path) -> None:
        _make_project(
            tmp_path,
            {
                "src/main.py": "pass",
                "src/lib.rs": "fn main() {}",
                "README.md": "# hello",
            },
        )
        files = collect_files(tmp_path)
        names = [str(f.relative_to(tmp_path)) for f in files]
        assert "src/main.py" in names
        assert "src/lib.rs" in names
        assert "README.md" not in names

    def test_returns_empty_when_no_matches(self, tmp_path: Path) -> None:
        _make_project(
            tmp_path,
            {
                "data.json": "{}",
            },
        )
        files = collect_files(tmp_path)
        assert files == []


# ---------------------------------------------------------------------------
# CompileResult
# ---------------------------------------------------------------------------


class TestCompileResult:
    def test_ok_property(self) -> None:
        assert CompileResult().ok is True
        assert CompileResult(error="fail").ok is False
