"""
memory.py — Durable, keyword-ranked memory store for EAGV3 Week 6.

Public API
----------
remember(text, *, source, run_id, goal_id=None)
    Persist a free-text entry (user query, preference, fact …).

record_outcome(*, tool_call, result_text, artifact_id, run_id, goal_id)
    Persist a tool-call result as a "tool_outcome" entry.

read(query, history, *, kinds=None, top_k=8) -> list[MemoryItem]
    Keyword-overlap retrieval: scores against each entry's keyword list
    and descriptor, returns ranked top-k.

Storage
-------
All entries live in a single JSON file at  state/memory.json  (a JSON array).
Uses Pydantic MemoryItem for typed contracts.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any  # used for value: dict[str, Any] and tool_call: Any

from schemas import HistoryEntry, MemoryItem

# ── storage path ──────────────────────────────────────────────────────────────
_STORE_PATH = Path(__file__).parent / "state" / "memory.json"
_lock = threading.Lock()

# ── stop-word set (English) ───────────────────────────────────────────────────
_STOP: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "i", "you", "he",
    "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "what", "which", "who", "this",
    "that", "these", "those", "not", "no", "so", "if", "as", "up", "out",
    "about", "into", "than", "then", "there", "also", "just", "how", "get",
})


# ── helpers ───────────────────────────────────────────────────────────────────
def _new_id(prefix: str = "mem") -> str:
    return f"{prefix}:{uuid.uuid4().hex[:8]}"


def _tokenise(text: str) -> set[str]:
    """Lowercase alpha-numeric tokens with stop-words removed."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in _STOP and len(t) > 1}


def _extract_keywords(*texts: str) -> list[str]:
    """Union of significant tokens across all provided strings, sorted."""
    bag: set[str] = set()
    for t in texts:
        bag |= _tokenise(t)
    return sorted(bag)


def _source_to_kind(source: str) -> str:
    """Map a free-form source tag to a valid MemoryItem kind Literal."""
    if source == "tool_outcome":
        return "tool_outcome"
    if source in ("preference", "scratchpad"):
        return source
    # user_query, fact, and any other source → "fact"
    return "fact"


# ── persistence helpers ───────────────────────────────────────────────────────
def _load_all() -> list[MemoryItem]:
    if not _STORE_PATH.exists():
        return []
    try:
        with _lock:
            raw: list[dict[str, Any]] = json.loads(
                _STORE_PATH.read_text(encoding="utf-8")
            )
    except (json.JSONDecodeError, OSError):
        return []
    items: list[MemoryItem] = []
    for obj in raw:
        try:
            obj.pop("score", None)
            items.append(MemoryItem.model_validate(obj))
        except Exception:
            pass
    return items


def _append(item: MemoryItem) -> None:
    # exclude transient score field from persistence
    row = item.model_dump(mode="json", exclude={"score"})
    with _lock:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if _STORE_PATH.exists():
            try:
                existing = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append(row)
        _STORE_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ── public API ────────────────────────────────────────────────────────────────
def remember(
    text: str,
    *,
    source: str,
    run_id: str,
    goal_id: str | None = None,
    confidence: float = 1.0,
) -> MemoryItem:
    """Persist a free-text memory entry (user query, fact, preference …)."""
    item = MemoryItem(
        id=_new_id("mem"),
        kind=_source_to_kind(source),   # type: ignore[arg-type]
        keywords=_extract_keywords(text, source),
        descriptor=text[:200],
        value={"text": text},
        artifact_id=None,
        source=source,
        run_id=run_id,
        goal_id=goal_id,
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )
    _append(item)
    return item


def record_outcome(
    *,
    tool_call: Any,
    result_text: str,
    artifact_id: str | None,
    run_id: str,
    goal_id: str,
    confidence: float = 1.0,
) -> MemoryItem:
    """Persist a tool-call result as a 'tool_outcome' memory entry."""
    tool_name: str = getattr(tool_call, "name", "")
    arguments: dict[str, Any] = {}
    if hasattr(tool_call, "arguments"):
        try:
            arguments = dict(tool_call.arguments)
        except Exception:
            arguments = {"raw": str(tool_call.arguments)}

    try:
        args_str = json.dumps(arguments, ensure_ascii=False)
    except Exception:
        args_str = str(arguments)

    descriptor = f"{tool_name}({args_str})"
    keywords = _extract_keywords(tool_name, args_str, result_text[:500])

    item = MemoryItem(
        id=_new_id("mem"),
        kind="tool_outcome",
        keywords=keywords,
        descriptor=descriptor,
        value={
            "tool": tool_name,
            "arguments": arguments,
            "result_preview": result_text[:300],
        },
        artifact_id=artifact_id or None,
        source="tool_outcome",
        run_id=run_id,
        goal_id=goal_id,
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )
    _append(item)
    return item


def read(
    query: str,
    history: list[HistoryEntry],
    *,
    kinds: list[str] | None = None,
    top_k: int = 8,
) -> list[MemoryItem]:
    """Return up to *top_k* memory items ranked by keyword overlap.

    Scoring
    -------
    query_bag  = tokenise(query)
               + tokenise(last 5 history descriptors)
    score      = |query_bag ∩ entry.keywords| / |query_bag ∪ entry.keywords|
                 (Jaccard — stable across entries of varying length)

    Parameters
    ----------
    query   : The current user query string.
    history : The agent's in-flight history list (HistoryEntry or dicts with
              optional 'result_descriptor' keys).
    kinds   : Optional whitelist of ``kind`` values.
              ``None`` means all kinds are eligible.
    top_k   : Maximum number of entries to return (default 8).
    """
    all_items = _load_all()
    if not all_items:
        return []

    # ── build the query token bag ─────────────────────────────────────────────
    query_bag: set[str] = _tokenise(query)

    for h in history[-5:]:
        descriptor = h.result_descriptor or h.text or ""
        query_bag |= _tokenise(descriptor)

    if not query_bag:
        eligible = [e for e in all_items if kinds is None or e.kind in kinds]
        return eligible[-top_k:]

    # ── score every eligible entry against its keyword list + descriptor ──────
    scored: list[MemoryItem] = []
    for item in all_items:
        if kinds is not None and item.kind not in kinds:
            continue
        entry_tokens = set(item.keywords) | _tokenise(item.descriptor)
        overlap = len(query_bag & entry_tokens)
        if overlap == 0:
            continue
        union = len(query_bag | entry_tokens)
        item.score = (overlap / union if union else 0.0) * item.confidence
        scored.append(item)

    scored.sort(key=lambda e: e.score, reverse=True)
    return scored[:top_k]
