"""Build raw_nodes from parsed CodeNodes for Engine.compile().

Converts the AST-extracted CodeNodes into the dict format expected by
``Engine.compile(raw_nodes=...)``:

    {"title": str, "content": str, "level": int}

Tree structure:
  Level 1: file path (section header, content = language info)
  Level 2: top-level semantic nodes (functions, classes, etc.)
  Level 3+: nested definitions (methods inside classes, etc.)
"""

from __future__ import annotations

from vectorless_code.ast_parser import CodeNode


def build_raw_nodes(
    file_nodes: list[tuple[str, str, list[CodeNode]]],
) -> list[dict]:
    """Convert per-file CodeNodes into raw_nodes for Engine.compile().

    Args:
        file_nodes: List of (file_path, language, nodes) tuples.

    Returns:
        List of dicts with title, content, level keys.
    """
    raw_nodes: list[dict] = []

    for file_path, language, nodes in file_nodes:
        raw_nodes.append({
            "title": file_path,
            "content": f"Language: {language}",
            "level": 1,
        })
        _append_nodes(raw_nodes, nodes, base_level=2)

    return raw_nodes


def _append_nodes(raw_nodes: list[dict], nodes: list[CodeNode], base_level: int) -> None:
    for node in nodes:
        raw_nodes.append({
            "title": f"{node.node_type}: {node.name}",
            "content": node.content,
            "level": base_level,
        })
        if node.children:
            _append_nodes(raw_nodes, node.children, base_level + 1)
