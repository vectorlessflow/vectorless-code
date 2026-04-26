"""Tests for AST parser, raw_nodes builder, and fingerprint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vectorless_code.ast_parser import (
    CodeNode,
    fallback_split,
    parse_file,
    SPLITTABLE_NODE_TYPES,
)
from vectorless_code.fingerprint import (
    detect_changes,
    file_hash,
    load_hashes,
    project_fingerprint,
    save_hashes,
)
from vectorless_code.raw_nodes import build_raw_nodes


# ---------------------------------------------------------------------------
# SPLITTABLE_NODE_TYPES coverage
# ---------------------------------------------------------------------------


class TestSplittableNodeTypes:
    def test_all_supported_languages_have_types(self):
        expected = {
            "python", "rust", "go", "javascript", "typescript",
            "java", "c", "cpp", "ruby", "swift", "kotlin", "scala",
        }
        assert set(SPLITTABLE_NODE_TYPES.keys()) == expected

    def test_types_are_sets_of_strings(self):
        for lang, types in SPLITTABLE_NODE_TYPES.items():
            assert isinstance(types, set), f"{lang} types should be a set"
            for t in types:
                assert isinstance(t, str), f"{lang}: {t!r} should be str"


# ---------------------------------------------------------------------------
# Fallback splitting
# ---------------------------------------------------------------------------


class TestFallbackSplit:
    def test_simple_function(self):
        code = "def hello():\n    print('hello')\n"
        nodes = fallback_split(code, "test.py", "python")
        assert len(nodes) >= 1
        assert "def hello" in nodes[0].content or "print" in nodes[0].content

    def test_multiple_blocks(self):
        code = "import os\n\n\ndef foo():\n    pass\n\n\nclass Bar:\n    pass\n"
        nodes = fallback_split(code, "test.py", "python")
        assert len(nodes) >= 2

    def test_empty_content(self):
        nodes = fallback_split("", "empty.py", "python")
        assert nodes == []

    def test_single_line(self):
        nodes = fallback_split("x = 1\n", "test.py", "python")
        assert len(nodes) == 1
        assert nodes[0].content == "x = 1"

    def test_no_trailing_newline(self):
        nodes = fallback_split("x = 1\ny = 2", "test.py", "python")
        assert len(nodes) >= 1

    def test_node_fields(self):
        code = "def foo():\n    pass\n"
        nodes = fallback_split(code, "test.py", "python")
        node = nodes[0]
        assert node.language == "python"
        assert node.node_type == "block"
        assert node.start_line >= 1
        assert node.end_line >= node.start_line
        assert node.children == []


# ---------------------------------------------------------------------------
# parse_file (without tree-sitter installed — tests fallback path)
# ---------------------------------------------------------------------------


class TestParseFileFallback:
    def test_unknown_language_uses_fallback(self):
        code = "some code here\n"
        nodes = parse_file("test.xyz", code, "unknown")
        assert len(nodes) >= 1
        assert nodes[0].node_type == "block"

    def test_parse_preserves_content(self):
        code = "def hello():\n    return 42\n"
        nodes = parse_file("test.py", code, "python")
        combined = "\n".join(n.content for n in nodes)
        assert "hello" in combined


# ---------------------------------------------------------------------------
# parse_file (with tree-sitter — only runs if tree-sitter is installed)
# ---------------------------------------------------------------------------


def _ts_available():
    try:
        from tree_sitter import Language  # noqa: F401
        import tree_sitter_python  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(
    not _ts_available(),
    reason="tree-sitter not installed",
)
class TestParseFileAST:
    def test_python_function(self):
        code = "def hello():\n    print('hello')\n"
        nodes = parse_file("test.py", code, "python")
        assert len(nodes) >= 1
        func_nodes = [n for n in nodes if n.node_type == "function_definition"]
        assert len(func_nodes) >= 1
        assert func_nodes[0].name == "hello"
        assert "print" in func_nodes[0].content

    def test_python_class_with_methods(self):
        code = "class App:\n    def __init__(self):\n        pass\n\n    def run(self):\n        pass\n"
        nodes = parse_file("test.py", code, "python")
        class_nodes = [n for n in nodes if n.node_type == "class_definition"]
        assert len(class_nodes) == 1
        assert class_nodes[0].name == "App"
        assert len(class_nodes[0].children) == 2

        method_names = [c.name for c in class_nodes[0].children]
        assert "__init__" in method_names
        assert "run" in method_names

    def test_python_async_function(self):
        code = "async def fetch():\n    await something()\n"
        nodes = parse_file("test.py", code, "python")
        async_nodes = [n for n in nodes if n.node_type == "async_function_definition"]
        assert len(async_nodes) >= 1

    def test_rust_function(self):
        try:
            import tree_sitter_rust  # noqa: F401
        except ImportError:
            pytest.skip("tree-sitter-rust not installed")

        code = "fn main() {\n    println!(\"hello\");\n}\n"
        nodes = parse_file("main.rs", code, "rust")
        assert len(nodes) >= 1
        func_nodes = [n for n in nodes if n.node_type == "function_item"]
        assert len(func_nodes) >= 1
        assert func_nodes[0].name == "main"


# ---------------------------------------------------------------------------
# raw_nodes builder
# ---------------------------------------------------------------------------


class TestBuildRawNodes:
    def test_single_file(self):
        nodes = [
            CodeNode(
                name="hello",
                node_type="function_definition",
                content="def hello():\n    pass",
                start_line=1,
                end_line=2,
                language="python",
            ),
        ]
        raw = build_raw_nodes([("src/main.py", "python", nodes)])
        assert len(raw) == 2  # file header + function

        assert raw[0]["title"] == "src/main.py"
        assert raw[0]["level"] == 1
        assert "python" in raw[0]["content"]

        assert raw[1]["title"] == "function_definition: hello"
        assert raw[1]["level"] == 2
        assert "def hello" in raw[1]["content"]

    def test_nested_nodes(self):
        children = [
            CodeNode(
                name="__init__",
                node_type="function_definition",
                content="def __init__(self): pass",
                start_line=2,
                end_line=2,
                language="python",
            ),
        ]
        parent = CodeNode(
            name="App",
            node_type="class_definition",
            content="class App:\n    def __init__(self): pass",
            start_line=1,
            end_line=2,
            language="python",
            children=children,
        )
        raw = build_raw_nodes([("app.py", "python", [parent])])
        assert len(raw) == 3  # file + class + method

        assert raw[1]["level"] == 2  # class at level 2
        assert raw[2]["level"] == 3  # method at level 3

    def test_empty_files(self):
        raw = build_raw_nodes([])
        assert raw == []

    def test_multiple_files(self):
        files = [
            ("a.py", "python", [CodeNode("f", "function_definition", "def f(): pass", 1, 1, "python")]),
            ("b.py", "python", [CodeNode("g", "function_definition", "def g(): pass", 1, 1, "python")]),
        ]
        raw = build_raw_nodes(files)
        assert len(raw) == 4  # 2 files × (header + function)

        titles = [r["title"] for r in raw]
        assert "a.py" in titles
        assert "b.py" in titles


# ---------------------------------------------------------------------------
# fingerprint
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_file_hash_deterministic(self):
        assert file_hash("hello") == file_hash("hello")
        assert file_hash("hello") != file_hash("world")

    def test_file_hash_accepts_bytes(self):
        assert file_hash(b"hello") == file_hash("hello")

    def test_project_fingerprint_deterministic(self):
        hashes = {"a.py": "abc", "b.py": "def"}
        assert project_fingerprint(hashes) == project_fingerprint(hashes)

    def test_project_fingerprint_order_independent(self):
        h1 = project_fingerprint({"a.py": "abc", "b.py": "def"})
        h2 = project_fingerprint({"b.py": "def", "a.py": "abc"})
        assert h1 == h2

    def test_save_and_load(self, tmp_path):
        hashes = {"src/main.py": "abc123", "src/utils.py": "def456"}
        save_hashes(tmp_path, hashes)
        loaded = load_hashes(tmp_path)
        assert loaded == hashes

    def test_load_nonexistent(self, tmp_path):
        assert load_hashes(tmp_path) == {}

    def test_detect_changes_all_new(self):
        current = {"a.py": "h1", "b.py": "h2"}
        changed, unchanged, removed = detect_changes(current, {})
        assert changed == ["a.py", "b.py"]
        assert unchanged == []
        assert removed == []

    def test_detect_changes_no_changes(self):
        hashes = {"a.py": "h1", "b.py": "h2"}
        changed, unchanged, removed = detect_changes(hashes, hashes)
        assert changed == []
        assert sorted(unchanged) == ["a.py", "b.py"]
        assert removed == []

    def test_detect_changes_mixed(self):
        current = {"a.py": "h1_new", "b.py": "h2", "c.py": "h3"}
        prev = {"a.py": "h1", "b.py": "h2", "d.py": "h4"}
        changed, unchanged, removed = detect_changes(current, prev)
        assert "a.py" in changed  # modified
        assert "c.py" in changed  # new
        assert unchanged == ["b.py"]  # unchanged
        assert removed == ["d.py"]  # removed
