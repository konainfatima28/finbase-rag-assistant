"""Retrieval confidence + abstain gate (PROMPT.md §6.7). Thresholds come from config/thresholds.json,
written by `python -m eval.calibrate` (maximises abstention F1 on the golden set)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.retrieval.context import Candidate
from app.text.tokenize import tokenize

#: tokens too generic to signal topical coverage
GENERIC = frozenset(
    [
        "finbase",
        "fee",
        "charge",
        "charges",
        "rate",
        "rates",
        "limit",
        "limits",
        "account",
        "card",
        "bank",
        "policy",
        "section",
        "much",
        "many",
        "long",
        "time",
        "day",
        "days",
        "month",
        "months",
        "year",
        "years",
        "get",
        "pay",
        "paid",
    ]
)


@dataclass(frozen=True)
class GateConfig:
    """Calibrated parameters."""

    weights: dict[str, float]
    dense_lo: float
    dense_hi: float
    abstain_threshold: float
    high: float
    medium: float
    calibrated: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateConfig:
        """Load from thresholds.json content."""
        return cls(
            weights={k: float(v) for k, v in data["weights"].items()},
            dense_lo=float(data["dense_lo"]),
            dense_hi=float(data["dense_hi"]),
            abstain_threshold=float(data["abstain_threshold"]),
            high=float(data["labels"]["high"]),
            medium=float(data["labels"]["medium"]),
            calibrated=bool(data.get("calibrated", False)),
        )


@dataclass
class Confidence:
    """Retrieval confidence with its feature breakdown."""

    score: float
    features: dict[str, float]
    abstain: bool

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly."""
        return {
            "score": round(self.score, 4),
            "features": {k: round(v, 4) for k, v in self.features.items()},
            "abstain": self.abstain,
        }


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def lexical_overlap(query: str, blocks: Sequence[Candidate]) -> float:
    """Share of the query's specific content tokens that occur in the selected context."""
    terms = {t for t in tokenize(query) if t not in GENERIC and len(t) > 1}
    if not terms:
        return 0.0
    context: set[str] = set()
    for block in blocks:
        context.update(tokenize(block.chunk.embed_text))
    return len(terms & context) / len(terms)


def features(
    query: str, ranked: Sequence[Candidate], blocks: Sequence[Candidate], config: GateConfig
) -> dict[str, float]:
    """Gate features in [0, 1]."""
    rerank_scores = [c.rerank for c in ranked if c.rerank is not None]
    dense_scores = [c.dense for c in ranked if c.dense is not None]
    top_rerank = rerank_scores[0] if rerank_scores else 0.0
    gap = (rerank_scores[0] - rerank_scores[1]) if len(rerank_scores) > 1 else top_rerank
    top_dense = max(dense_scores) if dense_scores else 0.0
    dense_scaled = _clip((top_dense - config.dense_lo) / max(1e-6, config.dense_hi - config.dense_lo))
    return {
        "rerank": _clip(top_rerank),
        "dense": dense_scaled,
        "gap": _clip(gap),
        "lexical": lexical_overlap(query, blocks),
        "has_rerank": 1.0 if rerank_scores else 0.0,
    }


def score(feats: dict[str, float], config: GateConfig) -> float:
    """Weighted combination; without a reranker its weight is redistributed to the other features."""
    weights = dict(config.weights)
    if not feats.get("has_rerank"):
        weights.pop("rerank", None)
    total = sum(weights.values()) or 1.0
    return _clip(sum(feats.get(name, 0.0) * w for name, w in weights.items()) / total)


def evaluate(
    query: str, ranked: Sequence[Candidate], blocks: Sequence[Candidate], config: GateConfig
) -> Confidence:
    """Compute confidence and the abstain decision."""
    feats = features(query, ranked, blocks, config)
    value = score(feats, config) if blocks else 0.0
    return Confidence(score=value, features=feats, abstain=(not blocks) or value < config.abstain_threshold)


def label(value: float, config: GateConfig) -> str:
    """High / Medium / Low."""
    if value >= config.high:
        return "High"
    if value >= config.medium:
        return "Medium"
    return "Low"
