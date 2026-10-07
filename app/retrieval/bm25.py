"""Okapi BM25 over the chunk list (own implementation: JSON-serialisable, no pickle, deterministic)."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np
import numpy.typing as npt


class BM25:
    """BM25 index. Document order is the chunk order of the index (same as FAISS owners)."""

    def __init__(self, doc_freqs: list[dict[str, int]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_freqs = doc_freqs
        self.doc_len = np.array([sum(d.values()) for d in doc_freqs], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if len(doc_freqs) else 0.0
        n = len(doc_freqs)
        df: Counter[str] = Counter()
        for doc in doc_freqs:
            df.update(doc.keys())
        # BM25+ style non-negative idf
        self.idf = {term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for idx, doc in enumerate(doc_freqs):
            for term, tf in doc.items():
                self.postings.setdefault(term, []).append((idx, tf))

    @classmethod
    def build(cls, tokenized_docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> BM25:
        """Build from token lists."""
        return cls([dict(Counter(tokens)) for tokens in tokenized_docs], k1=k1, b=b)

    def __len__(self) -> int:
        return len(self.doc_freqs)

    def scores(self, query_tokens: list[str]) -> npt.NDArray[np.float32]:
        """BM25 score of every document for the query."""
        out = np.zeros(len(self.doc_freqs), dtype=np.float32)
        if not self.doc_freqs:
            return out
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / max(self.avgdl, 1e-9))
        for term in set(query_tokens):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for idx, tf in self.postings[term]:
                out[idx] += idf * tf * (self.k1 + 1) / (tf + norm[idx])
        return out

    def top_k(self, query_tokens: list[str], k: int) -> list[tuple[int, float]]:
        """Top-k (doc index, score) with score > 0, ties broken by index for determinism."""
        scores = self.scores(query_tokens)
        order = sorted((i for i in range(len(scores)) if scores[i] > 0), key=lambda i: (-float(scores[i]), i))
        return [(i, float(scores[i])) for i in order[:k]]

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form."""
        return {"k1": self.k1, "b": self.b, "doc_freqs": self.doc_freqs}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BM25:
        """Inverse of `to_dict`."""
        return cls(
            [{str(k): int(v) for k, v in d.items()} for d in data["doc_freqs"]],
            k1=float(data["k1"]),
            b=float(data["b"]),
        )
