"""
schemas.py — Pydantic v2 contracts for every cognitive layer boundary.

All inter-module data is typed through these models.
No free-form dict passing between memory / perception / decision / action.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Memory ────────────────────────────────────────────────────────────────────

class MemoryItem(BaseModel):
    id: str
    kind: Literal["fact", "preference", "tool_outcome", "scratchpad"]
    keywords: list[str]
    descriptor: str              # one short human-readable line
    value: dict[str, Any]        # structured payload
    artifact_id: str | None      # handle into the artifact store
    source: str                  # raw origin tag (e.g. "user_query", "tool")
    run_id: str
    goal_id: str | None
    confidence: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Transient relevance score set by memory.read(); never persisted.
    score: float = Field(default=0.0, exclude=True)


# ── Artifact ──────────────────────────────────────────────────────────────────

class Artifact(BaseModel):
    id: str                      # "art:<sha256-prefix>"
    content_type: str
    size_bytes: int
    source: str
    descriptor: str


# ── Perception ────────────────────────────────────────────────────────────────

class Goal(BaseModel):
    id: str
    text: str                    # short imperative description
    done: bool
    attach_artifact_id: str | None


class Observation(BaseModel):
    goals: list[Goal]

    @property
    def all_done(self) -> bool:
        """True when every Goal in the list is marked done."""
        return bool(self.goals) and all(g.done for g in self.goals)

    def next_unfinished(self) -> Goal | None:
        """Return the first Goal where done=False, or None if all are done."""
        for g in self.goals:
            if not g.done:
                return g
        return None


# ── History ───────────────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    iter: int
    kind: Literal["answer", "action"]
    goal_id: str
    # answer entries
    text: str | None = None
    # action entries
    tool: str | None = None
    arguments: dict[str, Any] | None = None
    result_descriptor: str | None = None
    artifact_id: str | None = None


# ── Decision ──────────────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]


class DecisionOutput(BaseModel):
    answer: str | None = None      # populated when Decision can answer directly
    tool_call: ToolCall | None = None  # populated when a tool must be called

    @property
    def is_answer(self) -> bool:
        """Convenience: True when an inline answer is available."""
        return self.answer is not None
