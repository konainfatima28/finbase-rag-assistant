"""Answer confidence (PROMPT.md §7.2.4): retrieval confidence + citation coverage + figure verification."""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.gate import GateConfig, label


@dataclass
class AnswerConfidence:
    """Numeric score and High/Medium/Low label."""

    score: float
    label: str
    retrieval: float
    citation_coverage: float
    verified_rate: float


def combine(
    retrieval: float, coverage: float, verified_rate: float, has_citations: bool, config: GateConfig
) -> AnswerConfidence:
    """0.5 retrieval + 0.25 coverage + 0.25 verification; an uncited answer is capped at 0.3 (Low)."""
    value = 0.5 * retrieval + 0.25 * coverage + 0.25 * verified_rate
    if not has_citations:
        value = min(value, 0.3)
    if verified_rate < 1.0:
        value = min(value, config.high - 0.01)  # never "High" with an unverified figure
    value = max(0.0, min(1.0, value))
    return AnswerConfidence(
        round(value, 4),
        label(value, config),
        round(retrieval, 4),
        round(coverage, 4),
        round(verified_rate, 4),
    )
