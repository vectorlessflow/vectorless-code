"""Ask questions about a compiled codebase."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from vectorless_code.engine import create_engine
from vectorless_code.settings import UserSettings

if TYPE_CHECKING:
    from vectorless.ask.types import Evidence, Output

logger = logging.getLogger(__name__)


async def ask_codebase(
    question: str,
    doc_ids: list[str] | None = None,
    *,
    user_settings: UserSettings | None = None,
    on_progress: Callable[[str], None] | None = None,
    timeout_secs: int = 120,
) -> Output:
    """Ask a question about a codebase using vectorless.

    Args:
        question: Natural language question.
        doc_ids: Limit query to specific document IDs. None queries all.
        user_settings: User settings (API key, model). Loaded if not provided.
        on_progress: Optional callback for progress updates.
        timeout_secs: Per-operation timeout.

    Returns:
        vectorless Output with answer, evidence, and confidence.

    Raises:
        RuntimeError: If no API key is configured.
    """
    engine = create_engine(user_settings)

    async with engine:
        stream = await engine.query_stream(
            question=question,
            doc_ids=doc_ids,
            timeout_secs=timeout_secs,
        )

        async for event in stream:
            if on_progress:
                event_type = event.get("type", "")
                if event_type == "progress":
                    on_progress(event.get("message", ""))

    output: Output = stream.result
    logger.info(
        "Ask completed: confidence=%.2f, evidence_count=%d",
        output.confidence,
        len(output.evidence),
    )
    return output


def format_output(output: Output) -> str:
    """Format an Output for terminal display."""
    lines: list[str] = []
    lines.append(output.answer)
    lines.append("")

    if output.evidence:
        lines.append(f"--- Evidence ({len(output.evidence)} sources) ---")
        for i, ev in enumerate(output.evidence, 1):
            source = ev.source_path or ev.doc_name or "unknown"
            lines.append(f"\n[{i}] {source}")
            if ev.node_title:
                lines.append(f"    {ev.node_title}")
            if ev.content:
                preview = ev.content.strip().splitlines()[:3]
                for line in preview:
                    lines.append(f"    {line}")

    if output.confidence > 0:
        lines.append(f"\nConfidence: {output.confidence:.0%}")

    return "\n".join(lines)
