"""Reciprocal Rank Fusion + post-fusion weights (PROMPT.md §6.4)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def rrf(rankings: Mapping[str, Sequence[tuple[int, float]]], k: int = 60) -> dict[int, float]:
    """RRF score per doc: sum over rankings of 1 / (k + rank), rank starting at 1."""
    fused: dict[int, float] = {}
    for ranking in rankings.values():
        for rank, (doc, _score) in enumerate(ranking, start=1):
            fused[doc] = fused.get(doc, 0.0) + 1.0 / (k + rank)
    return fused


def apply_weights(scores: Mapping[int, float], weights: Mapping[int, float]) -> dict[int, float]:
    """Multiply each score by its weight (default 1.0)."""
    return {doc: score * weights.get(doc, 1.0) for doc, score in scores.items()}


def ranked(scores: Mapping[int, float]) -> list[tuple[int, float]]:
    """Sort by score desc, then index asc (deterministic)."""
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
