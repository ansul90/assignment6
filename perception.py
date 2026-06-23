"""
perception.py — Perception module for EAGV3 Week 6.

Responsibilities:
1. Decompose query into sequential Goals if prior_goals is empty.
2. Check history to mark Goals as done.
3. Attach artifact ID to the first unfinished Goal if needed.
4. Preserve Goal order.

All inputs and outputs use typed Pydantic contracts from schemas.py.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from llm_gatewayV3.client import LLM
from schemas import Goal, HistoryEntry, MemoryItem, Observation

# ── LLM Client singleton ──────────────────────────────────────────────────────
_llm = LLM()


# ── helpers ───────────────────────────────────────────────────────────────────
def _new_goal_id() -> str:
    return f"g:{uuid.uuid4().hex[:8]}"


def _tokenise(text: str) -> set[str]:
    """Lowercase alpha-numeric tokens with stop-words removed."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "from"}
    return {t for t in tokens if t not in stop and len(t) > 1}


# ── private LLM-powered functions ─────────────────────────────────────────────

def _decompose(query: str, hits: list[MemoryItem]) -> list[Goal]:
    """Decompose a query into one or more bounded goals using Gemini."""
    hits_summary = "\n".join(
        f"- [{e.kind}] {e.descriptor}" for e in hits
    ) or "No prior memory hits available."

    system_prompt = (
        "You are the Perception module of an agent. Your job is to decompose the user's high-level query "
        "into one or more bounded, sequential goals.\n"
        "Each goal must be a short, clear imperative statement describing WHAT information is needed.\n\n"
        "CRITICAL RULES:\n"
        "1. ALWAYS separate data retrieval from answer synthesis into distinct goals.\n"
        "   - If the query involves fetching a URL or web page, that fetch is its OWN goal.\n"
        "   - Extracting or synthesising information from that page is a SEPARATE subsequent goal.\n"
        "2. Never merge a fetch/search step with an extraction/answer step into one goal.\n"
        "3. Keep goals minimal — do not create more goals than the query requires.\n\n"
        "Return the goals as a JSON object matching the schema."
    )

    user_prompt = (
        f"User Query: {query}\n\n"
        f"Memory Hits (for context):\n{hits_summary}\n\n"
        "Decompose this query into a list of sequential goals."
    )

    schema = {
        "type": "object",
        "properties": {
            "goals": {
                "type": "array",
                "items": {
                    "type": "string",
                    "description": "A short imperative statement representing a single bounded goal."
                }
            }
        },
        "required": ["goals"],
        "additionalProperties": False
    }

    try:
        resp = _llm.chat(
            prompt=user_prompt,
            system=system_prompt,
            provider="g",
            response_format={
                "type": "json_schema",
                "schema": schema,
                "name": "decompose_goals",
                "strict": True
            },
            temperature=0.0
        )
        parsed = resp.get("parsed")
        if not parsed and resp.get("text"):
            parsed = json.loads(resp["text"])

        goal_texts: list[str] = parsed.get("goals", []) if parsed else []
    except Exception:
        goal_texts = []

    if not goal_texts:
        goal_texts = [query]

    return [
        Goal(id=_new_goal_id(), text=t, done=False, attach_artifact_id=None)
        for t in goal_texts
    ]


def _is_satisfied(goal: Goal, history: list[HistoryEntry]) -> bool:
    """Determine if a goal is satisfied based on history, with heuristic then LLM fallback."""
    goal_history = [h for h in history if h.goal_id == goal.id]
    if not goal_history:
        return False

    # Heuristic: if any entry is a real (non-error) answer, it is done
    ERROR_PREFIXES = ("[Decision error]", "[ERROR]", "[Error")
    if any(
        h.kind == "answer"
        and h.text
        and not any(h.text.startswith(p) for p in ERROR_PREFIXES)
        for h in goal_history
    ):
        return True

    # LLM fallback: ask Gemini if the actions taken satisfy the goal
    actions_summary = ""
    for h in goal_history:
        if h.kind == "action":
            actions_summary += (
                f"- Tool: {h.tool}\n"
                f"  Arguments: {h.arguments}\n"
                f"  Result: {h.result_descriptor}\n"
            )

    system_prompt = (
        "You are the Perception module of an agent. Determine if a specific goal has been "
        "fully satisfied based on the actions taken for it.\n"
        "If they successfully complete the goal, return {\"done\": true}.\n"
        "If the goal is not yet fully completed, return {\"done\": false}."
    )

    user_prompt = f"Goal: {goal.text}\n\nActions taken:\n{actions_summary}\n"

    schema = {
        "type": "object",
        "properties": {
            "done": {"type": "boolean"},
            "reason": {"type": "string"}
        },
        "required": ["done", "reason"],
        "additionalProperties": False
    }

    try:
        resp = _llm.chat(
            prompt=user_prompt,
            system=system_prompt,
            provider="g",
            response_format={
                "type": "json_schema",
                "schema": schema,
                "name": "goal_satisfaction",
                "strict": True
            },
            temperature=0.0
        )
        parsed = resp.get("parsed")
        if not parsed and resp.get("text"):
            parsed = json.loads(resp["text"])
        return parsed.get("done", False) if parsed else False
    except Exception:
        return False


def _find_artifact(goal: Goal, hits: list[MemoryItem]) -> str | None:
    """Pick the most-recent relevant artifact from memory hits.

    Deterministic: ranks candidates by keyword overlap between the goal text
    and the artifact's descriptor + keywords, then by recency. Prefers
    document-style artifacts (fetch_url, read_file) over search-result lists.
    """
    candidates = [
        entry for entry in hits
        if entry.kind == "tool_outcome" and entry.artifact_id
    ]
    if not candidates:
        return None

    goal_tokens = _tokenise(goal.text)
    DOCUMENT_TOOLS = {"fetch_url", "read_file"}

    def score(entry: MemoryItem) -> tuple[int, int, str]:
        entry_tokens = set(entry.keywords) | _tokenise(entry.descriptor)
        overlap = len(goal_tokens & entry_tokens)
        # Prefer document-style tools when relevance ties
        tool = entry.value.get("tool", "")
        tool_bonus = 1 if tool in DOCUMENT_TOOLS else 0
        return (overlap, tool_bonus, entry.created_at.isoformat())

    candidates.sort(key=score, reverse=True)
    best = candidates[0]
    # Only attach if there's at least some keyword overlap OR the artifact
    # came from a document-fetching tool (which the agent likely needs)
    best_score = score(best)
    if best_score[0] > 0 or best_score[1] > 0:
        return best.artifact_id
    return None


# ── public API ────────────────────────────────────────────────────────────────

def observe(
    query: str,
    hits: list[MemoryItem],
    history: list[HistoryEntry],
    prior_goals: list[Goal],
    run_id: str,
) -> Observation:
    """Observe the environment, update goals, and decide on artifact attachment."""
    # Obligation 1: decompose on first iteration
    if not prior_goals:
        goals = _decompose(query, hits)
    else:
        # Obligation 4: preserve order — copy, do not reorder/insert/drop
        goals = [g.model_copy() for g in prior_goals]

    # Obligation 2: mark done by examining history
    goals = [
        g.model_copy(update={"done": True}) if not g.done and _is_satisfied(g, history) else g
        for g in goals
    ]

    # Obligation 3: attach artifact to the first unfinished goal only
    updated: list[Goal] = []
    first_unfinished_found = False
    for g in goals:
        if not g.done and not first_unfinished_found:
            first_unfinished_found = True
            art_id = _find_artifact(g, hits)
            updated.append(g.model_copy(update={"attach_artifact_id": art_id}))
        else:
            updated.append(g.model_copy(update={"attach_artifact_id": None}))

    return Observation(goals=updated)
