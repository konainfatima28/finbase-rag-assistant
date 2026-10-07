"""On-disk embedding cache keyed by (provider, model, sha256(text)) — makes ingestion idempotent and cheap."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.providers.base import EmbeddingProvider, EmbedKind, Vectors


def cache_key(provider: str, model: str, kind: str, text: str) -> str:
    """Stable cache key."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"{provider}|{model}|{kind}|{digest}"


class EmbeddingCache:
    """sqlite-backed vector cache (stdlib only, cross-platform)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, dim INTEGER, vec BLOB)")
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Vectors | None:
        """Cached vector or None."""
        row = self._conn.execute("SELECT dim, vec FROM vectors WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return np.frombuffer(row[1], dtype=np.float32).reshape(int(row[0]))

    def put_many(self, items: list[tuple[str, Vectors]]) -> None:
        """Insert/replace vectors."""
        self._conn.executemany(
            "INSERT OR REPLACE INTO vectors (key, dim, vec) VALUES (?, ?, ?)",
            [(key, int(vec.shape[0]), vec.astype(np.float32).tobytes()) for key, vec in items],
        )
        self._conn.commit()

    def clear(self) -> None:
        """Drop every cached vector."""
        self._conn.execute("DELETE FROM vectors")
        self._conn.commit()

    def close(self) -> None:
        """Close the connection."""
        self._conn.close()

    async def embed(self, provider: EmbeddingProvider, texts: Sequence[str], kind: EmbedKind) -> Vectors:
        """Embed with caching: only uncached texts hit the provider (one batched call)."""
        keys = [cache_key(provider.name, provider.model, kind, t) for t in texts]
        found: dict[int, Vectors] = {}
        missing: list[int] = []
        for i, key in enumerate(keys):
            vec = self.get(key)
            if vec is None:
                missing.append(i)
            else:
                found[i] = vec
        self.hits += len(found)
        self.misses += len(missing)
        if missing:
            fresh = await provider.embed([texts[i] for i in missing], kind)
            self.put_many([(keys[i], fresh[j]) for j, i in enumerate(missing)])
            for j, i in enumerate(missing):
                found[i] = fresh[j]
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        return np.vstack([found[i] for i in range(len(texts))]).astype(np.float32)
