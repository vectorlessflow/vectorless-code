"""Tests for vectorless_code.compile."""

from pathlib import Path

import pytest

from vectorless_code.compile import (
    CompileResult,
    build_virtual_document,
    collect_files,
    compile_project,
    detect_language,
    gather_stats,
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
# build_virtual_document
# ---------------------------------------------------------------------------


class TestBuildVirtualDocument:
    def test_creates_markers(self, tmp_path: Path) -> None:
        files = [
            tmp_path / "src/main.py",
            tmp_path / "src/lib.rs",
        ]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("content")

        doc = build_virtual_document(tmp_path, files)
        assert "@@@FILE:src/main.py@@@" in doc
        assert "@@@FILE:src/lib.rs@@@" in doc

    def test_includes_content(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.py"
        f.write_text("def hello(): pass")
        doc = build_virtual_document(tmp_path, [f])
        assert "def hello(): pass" in doc

    def test_sorted_by_path(self, tmp_path: Path) -> None:
        paths = ["z.py", "a.py", "m.py"]
        for name in paths:
            (tmp_path / name).write_text(f"# {name}")
        files = [tmp_path / n for n in paths]
        doc = build_virtual_document(tmp_path, files)
        # a.py marker should come before z.py marker
        assert doc.index("@@@FILE:a.py@@@") < doc.index("@@@FILE:z.py@@@")


# ---------------------------------------------------------------------------
# collect_files
# ---------------------------------------------------------------------------


class TestCollectFiles:
    def test_returns_matching_files(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "pass",
            "src/lib.rs": "fn main() {}",
            "README.md": "# hello",
        })
        files = collect_files(tmp_path)
        names = [str(f.relative_to(tmp_path)) for f in files]
        assert "src/main.py" in names
        assert "src/lib.rs" in names
        assert "README.md" not in names

    def test_returns_empty_when_no_matches(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "data.json": "{}",
        })
        files = collect_files(tmp_path)
        assert files == []


# ---------------------------------------------------------------------------
# gather_stats
# ---------------------------------------------------------------------------


class TestGatherStats:
    def test_counts_files(self, tmp_path: Path) -> None:
        files = []
        for name in ["a.py", "b.rs", "c.go"]:
            p = tmp_path / name
            p.write_text("line1\nline2\n")
            files.append(p)

        result = gather_stats(files, tmp_path)
        assert result.file_count == 3
        assert result.total_lines == 6  # 2 lines * 3 files
        assert result.languages == {"python": 1, "rust": 1, "go": 1}

    def test_empty_files(self, tmp_path: Path) -> None:
        result = gather_stats([], tmp_path)
        assert result.file_count == 0
        assert result.total_lines == 0


# ---------------------------------------------------------------------------
# compile_project
# ---------------------------------------------------------------------------


class TestCompileProject:
    def test_compiles_project(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "src/main.py": "def main():\n    pass\n",
            "src/lib.rs": "fn lib() -> i32 {\n    42\n}\n",
        })
        result = compile_project(tmp_path)
        assert result.ok
        assert result.file_count == 2
        assert result.total_lines > 0
        assert "python" in result.languages
        assert "rust" in result.languages

    def test_returns_error_when_no_files(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "data.json": "{}",
        })
        result = compile_project(tmp_path)
        assert not result.ok
        assert result.error is not None
        assert "No code files" in result.error

    def test_compile_result_ok_property(self) -> None:
        assert CompileResult().ok is True
        assert CompileResult(error="fail").ok is False
