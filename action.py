"""
action.py — Action module for EAGV3 Week 6.

Responsibilities:
1. Guard against artifact handles being passed as path/url arguments.
2. Dispatch the tool call to the MCP server via session.call_tool().
3. Collapse the result content blocks into a single text string.
4. If the payload exceeds ARTIFACT_THRESHOLD_BYTES, persist to ArtifactStore
   and return a short descriptor. Otherwise return the text directly.
"""
from __future__ import annotations

import uuid
from typing import Any

from mcp import ClientSession

from artifact_store import artifacts
from schemas import ToolCall

# Payloads larger than this are spilled into the artifact store
ARTIFACT_THRESHOLD_BYTES = 4 * 1024  # 4 KB


def _new_artifact_id() -> str:
    return f"art:{uuid.uuid4().hex[:8]}"


def _collapse_content(content: Any) -> str:
    """Collapse MCP result content blocks into a single plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif hasattr(block, "text"):
                parts.append(str(block.text))
            elif isinstance(block, dict):
                parts.append(block.get("text") or str(block))
        return "\n".join(parts)
    return str(content)


def _has_artifact_handle(arguments: dict[str, Any]) -> str | None:
    """Return the offending key if any argument value looks like an artifact handle."""
    for key, val in arguments.items():
        if isinstance(val, str) and val.startswith("art:"):
            return key
    return None


async def execute(
    session: ClientSession,
    tool_call: ToolCall,
) -> tuple[str, str | None]:
    """Execute a tool call via MCP and return (result_text, artifact_id|None).

    Returns
    -------
    (result_text, None)
        When the payload is ≤ ARTIFACT_THRESHOLD_BYTES. The full text is
        returned directly and no artifact is created.

    (descriptor, artifact_id)
        When the payload exceeds the threshold. The full bytes are persisted
        to the in-process ArtifactStore and a short human-readable descriptor
        is returned as result_text.

    ("[ERROR] ...", None)
        When an artifact handle is detected in the arguments, or when the
        MCP call itself raises an exception.
    """
    # ── guard: reject artifact handles in path/url arguments ─────────────────
    offending_key = _has_artifact_handle(tool_call.arguments)
    if offending_key is not None:
        handle = tool_call.arguments[offending_key]
        return (
            f"[ERROR] Argument '{offending_key}' contains an artifact handle "
            f"({handle!r}). Artifact handles are not file paths or URLs. "
            f"Use the ArtifactStore to read the content, not the tool directly.",
            None,
        )

    # ── dispatch to MCP ───────────────────────────────────────────────────────
    try:
        result = await session.call_tool(
            tool_call.name,
            arguments=tool_call.arguments,
        )
    except Exception as exc:
        return f"[ERROR] MCP tool '{tool_call.name}' raised: {exc}", None

    raw_text = _collapse_content(result.content)

    # ── threshold check ───────────────────────────────────────────────────────
    raw_bytes = raw_text.encode("utf-8")
    if len(raw_bytes) <= ARTIFACT_THRESHOLD_BYTES:
        return raw_text, None

    art_id = _new_artifact_id()
    artifacts.put(art_id, raw_bytes)
    preview = raw_text[:200].replace("\n", " ")
    descriptor = f"[artifact {art_id}, {len(raw_bytes):,} bytes] preview: {preview}"
    return descriptor, art_id
