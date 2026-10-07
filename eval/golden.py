"""Golden-set loading and validation (PROMPT.md §11.1)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.ingest.models import Chunk
from eval.metrics import golds

GOLDEN_PATH = Path(__file__).resolve().parent / "golden.jsonl"

#: category -> minimum count (single_fact is additionally >= 6 per document)
MINIMUMS = {
    "single_fact": 36,
    "cross_document": 6,
    "conflict": 6,
    "absent": 8,
    "unsupported": 4,
    "multi_turn": 4,
    "adversarial": 4,
    "garbled": 2,
    "hinglish": 3,
    "category_enumeration": 6,
}
REQUIRED_FIELDS = ("id", "category", "question", "answerable", "expected_facts", "gold_sections", "notes")


def load_golden(path: Path = GOLDEN_PATH) -> list[dict[str, Any]]:
    """Parse golden.jsonl (one JSON object per line)."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_golden(items: list[dict[str, Any]]) -> list[str]:
    """Schema and coverage problems (empty list = valid)."""
    problems: list[str] = []
    ids = [i.get("id") for i in items]
    if len(ids) != len(set(ids)):
        problems.append("duplicate ids")
    if len(items) < 70:
        problems.append(f"only {len(items)} items (< 70)")
    for item in items:
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            problems.append(f"{item.get('id')}: missing {missing}")
        if item.get("category") not in MINIMUMS:
            problems.append(f"{item.get('id')}: unknown category {item.get('category')}")
        if item.get("category") == "multi_turn" and not item.get("history"):
            problems.append(f"{item.get('id')}: multi_turn without history")
    counts = Counter(i.get("category") for i in items)
    for category, minimum in MINIMUMS.items():
        if counts[category] < minimum:
            problems.append(f"category {category}: {counts[category]} < {minimum}")
    per_doc = Counter(
        g["doc_id"] for i in items if i.get("category") == "single_fact" for g in i["gold_sections"][:1]
    )
    for doc, n in per_doc.items():
        if n < 6:
            problems.append(f"single_fact for {doc}: {n} < 6")
    if len(per_doc) < 6:
        problems.append(f"single_fact covers only {len(per_doc)} documents")
    return problems


def resolve_gold_chunk_ids(item: dict[str, Any], chunks: list[Chunk]) -> list[str]:
    """`gold_chunk_ids` resolved from `gold_sections` against the current index (section-level)."""
    units = golds(item)
    return [c.chunk_id for c in chunks if any(g.matches(c) for g in units)]
