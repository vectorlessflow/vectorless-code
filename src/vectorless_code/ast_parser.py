"""AST-level code parsing using tree-sitter.

Extracts semantic code nodes (functions, classes, methods, etc.) from source files
and produces raw_nodes for vectorless Engine.compile().

Strategy:
  1. If tree-sitter + language grammar installed → AST-level extraction
  2. Otherwise → line-based fallback splitting
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-language AST node types to extract as semantic units
# ---------------------------------------------------------------------------

SPLITTABLE_NODE_TYPES: dict[str, set[str]] = {
    "python": {
        "function_definition",
        "class_definition",
        "decorated_definition",
        "async_function_definition",
    },
    "rust": {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "mod_item",
        "type_alias",
        "const_item",
        "static_item",
    },
    "go": {
        "function_declaration",
        "method_declaration",
        "type_declaration",
    },
    "javascript": {
        "function_declaration",
        "arrow_function",
        "class_declaration",
        "method_definition",
    },
    "typescript": {
        "function_declaration",
        "arrow_function",
        "class_declaration",
        "method_definition",
        "interface_declaration",
        "type_alias_declaration",
    },
    "java": {
        "class_declaration",
        "interface_declaration",
        "method_declaration",
        "constructor_declaration",
        "enum_declaration",
    },
    "c": {
        "function_definition",
        "struct_specifier",
        "enum_specifier",
    },
    "cpp": {
        "function_definition",
        "class_specifier",
        "struct_specifier",
        "namespace_definition",
    },
    "ruby": {
        "method",
        "singleton_method",
        "class",
        "module",
    },
    "swift": {
        "function_declaration",
        "class_declaration",
        "struct_declaration",
        "protocol_declaration",
        "enum_declaration",
        "extension_declaration",
    },
    "kotlin": {
        "function_declaration",
        "class_declaration",
        "object_declaration",
        "interface_declaration",
        "companion_object",
    },
    "scala": {
        "function_definition",
        "class_definition",
        "object_definition",
        "trait_definition",
    },
}

# Map language → tree-sitter language package name
_LANG_PACKAGE_MAP: dict[str, str] = {
    "python": "tree_sitter_python",
    "rust": "tree_sitter_rust",
    "go": "tree_sitter_go",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "java": "tree_sitter_java",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "ruby": "tree_sitter_ruby",
    "swift": "tree_sitter_swift",
    "kotlin": "tree_sitter_kotlin",
    "scala": "tree_sitter_scala",
}

# ---------------------------------------------------------------------------
# CodeNode — a semantic code unit
# ---------------------------------------------------------------------------


@dataclass
class CodeNode:
    """A semantic code unit extracted from a source file."""

    name: str
    node_type: str
    content: str
    start_line: int
    end_line: int
    language: str
    children: list[CodeNode] = field(default_factory=list)


# ---------------------------------------------------------------------------
# tree-sitter language loading (lazy, graceful)
# ---------------------------------------------------------------------------

_language_cache: dict[str, object] = {}
_parser_cache: dict[str, object] = {}

_ts_available: bool | None = None


def _ts_is_available() -> bool:
    global _ts_available
    if _ts_available is None:
        try:
            from tree_sitter import Language  # noqa: F401

            _ts_available = True
        except ImportError:
            _ts_available = False
    return _ts_available


def get_language(lang_name: str) -> object | None:
    """Load a tree-sitter Language for *lang_name*.

    Returns ``None`` if tree-sitter or the specific grammar is not installed.
    """
    if not _ts_is_available():
        return None

    if lang_name in _language_cache:
        return _language_cache[lang_name]

    package_name = _LANG_PACKAGE_MAP.get(lang_name)
    if not package_name:
        return None

    try:
        import importlib

        from tree_sitter import Language

        mod = importlib.import_module(package_name)

        if lang_name == "typescript":
            lang = Language(mod.language_typescript())
        else:
            lang = Language(mod.language())

        _language_cache[lang_name] = lang
        return lang
    except (ImportError, AttributeError) as e:
        logger.debug("tree-sitter grammar not available for %s: %s", lang_name, e)
        return None


def _get_parser(lang_name: str) -> object | None:
    """Get a cached Parser for *lang_name*."""
    if lang_name in _parser_cache:
        return _parser_cache[lang_name]

    lang = get_language(lang_name)
    if lang is None:
        return None

    try:
        from tree_sitter import Parser

        parser = Parser(lang)
        _parser_cache[lang_name] = parser
        return parser
    except Exception:
        return None


# ---------------------------------------------------------------------------
# AST node extraction
# ---------------------------------------------------------------------------


def extract_nodes(tree: object, source_bytes: bytes, language: str) -> list[CodeNode]:
    """Walk a tree-sitter AST and extract semantic nodes."""
    splittable = SPLITTABLE_NODE_TYPES.get(language, set())
    if not splittable:
        return []

    root = tree.root_node
    return _walk_node(root, source_bytes, language, splittable)


def _walk_node(
    node: object,
    source: bytes,
    language: str,
    splittable: set[str],
) -> list[CodeNode]:
    results: list[CodeNode] = []

    if node.type in splittable:
        text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        name = _node_name(node, source)

        child_nodes: list[CodeNode] = []
        for child in node.children:
            child_nodes.extend(_walk_node(child, source, language, splittable))

        results.append(
            CodeNode(
                name=name,
                node_type=node.type,
                content=text,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                language=language,
                children=child_nodes,
            )
        )
    else:
        for child in node.children:
            results.extend(_walk_node(child, source, language, splittable))

    return results


def _node_name(node: object, source: bytes) -> str:
    """Extract a human-readable name from an AST node."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "property_identifier", "name"):
            return source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")

    if node.type == "decorated_definition":
        for child in node.children:
            name = _node_name(child, source)
            if name:
                return name

    if node.type == "impl_item":
        for child in node.children:
            if child.type == "type_identifier":
                return "impl " + source[child.start_byte : child.end_byte].decode(
                    "utf-8", errors="replace"
                )

    return node.type


# ---------------------------------------------------------------------------
# Fallback: line-based splitting
# ---------------------------------------------------------------------------


def fallback_split(content: str, file_path: str, language: str) -> list[CodeNode]:
    """Split on blank-line boundaries when AST parsing is unavailable."""
    lines = content.splitlines()
    if not lines:
        return []

    chunks: list[CodeNode] = []
    current_chunk: list[str] = []
    current_start = 0

    for i, line in enumerate(lines):
        if line.strip() == "" and current_chunk:
            chunks.append(
                CodeNode(
                    name=f"{file_path}:{current_start + 1}-{i}",
                    node_type="block",
                    content="\n".join(current_chunk),
                    start_line=current_start + 1,
                    end_line=i,
                    language=language,
                )
            )
            current_chunk = []
        else:
            if not current_chunk:
                current_start = i
            current_chunk.append(line)

    if current_chunk:
        chunks.append(
            CodeNode(
                name=f"{file_path}:{current_start + 1}-{len(lines)}",
                node_type="block",
                content="\n".join(current_chunk),
                start_line=current_start + 1,
                end_line=len(lines),
                language=language,
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Main parse entry point
# ---------------------------------------------------------------------------


def parse_file(file_path: str, content: str, language: str) -> list[CodeNode]:
    """Parse a single code file into semantic nodes.

    Strategy: AST parsing → fallback to line-based splitting.
    """
    splittable = SPLITTABLE_NODE_TYPES.get(language)

    if not splittable:
        return fallback_split(content, file_path, language)

    parser = _get_parser(language)
    if parser is None:
        return fallback_split(content, file_path, language)

    try:
        source_bytes = content.encode("utf-8")
        tree = parser.parse(source_bytes)
        nodes = extract_nodes(tree, source_bytes, language)

        if not nodes:
            return fallback_split(content, file_path, language)

        return nodes
    except Exception as e:
        logger.warning("tree-sitter parse failed for %s: %s, using fallback", file_path, e)
        return fallback_split(content, file_path, language)
