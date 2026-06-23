"""
artifact_store.py — Shared in-process byte-blob store.

Imported by both agent6.py (for exists/get_bytes) and action.py (for put),
so it lives in its own module to avoid the circular import
agent6 → action → agent6.
"""
from __future__ import annotations


class ArtifactStore:
    """Lightweight byte-blob store keyed by artifact_id."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put(self, artifact_id: str, data: bytes) -> None:
        self._store[artifact_id] = data

    def exists(self, artifact_id: str) -> bool:
        return artifact_id in self._store

    def get_bytes(self, artifact_id: str) -> bytes:
        return self._store[artifact_id]


# Module-level singleton shared by all importers in this process.
artifacts = ArtifactStore()
