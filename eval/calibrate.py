"""`python -m eval.calibrate`: calibrate the abstain gate on the golden set (PROMPT.md §6.7).

Runs retrieval (hybrid + rerank) for every golden item, then grid-searches gate weights and the abstain
threshold to maximise abstention F1 (positive = should abstain) subject to over-refusal <= 5% on
answerable items. The gate is only the FIRST abstention layer (the LLM's NOT_FOUND is the second), so the
constraint on over-refusal matters more than catching every unanswerable question here.
Writes config/thresholds.json (calibrated=true) and eval/results/calibration.json.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
from datetime import UTC, datetime
from typing import Any

from app.providers.factory import get_embedding_provider
from app.retrieval.gate import GateConfig, score
from app.retrieval.store import IndexStore
from app.settings import ROOT_DIR, get_settings
from eval.golden import load_golden
from eval.metrics import abstention_metrics
from eval.run import CachedQueryEmbedder, build_retriever, run_retrieval

MAX_OVER_REFUSAL = 0.05
WEIGHT_GRID = {
    "rerank": [0.3, 0.45, 0.6],
    "dense": [0.1, 0.25, 0.4],
    "gap": [0.0, 0.05],
    "lexical": [0.1, 0.25, 0.4],
}
THRESHOLDS = [round(t * 0.01, 2) for t in range(5, 81)]


def search(rows: list[dict[str, Any]], base: GateConfig) -> dict[str, Any]:
    """Best (weights, threshold) by F1 with the over-refusal constraint; ties -> lower over-refusal, lower threshold."""
    best: dict[str, Any] | None = None
    for values in itertools.product(*WEIGHT_GRID.values()):
        weights = dict(zip(WEIGHT_GRID, values, strict=True))
        cfg = GateConfig(weights, base.dense_lo, base.dense_hi, 0.0, base.high, base.medium)
        scores = [(not r["answerable"], score(r["features"], cfg)) for r in rows]
        for threshold in THRESHOLDS:
            metrics = abstention_metrics([(should, s < threshold) for should, s in scores])
            if metrics["over_refusal"] > MAX_OVER_REFUSAL:
                continue
            key = (metrics["f1"], -metrics["over_refusal"], -threshold)
            if best is None or key > best["key"]:
                best = {"key": key, "weights": weights, "threshold": threshold, "metrics": metrics}
    if best is None:
        raise SystemExit("no configuration satisfies the over-refusal constraint")
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate abstain-gate thresholds on the golden set")
    parser.add_argument("--dry-run", action="store_true", help="do not write config/thresholds.json")
    args = parser.parse_args(argv)
    settings = get_settings()
    store = IndexStore.load(settings.index_dir, settings)
    inner = get_embedding_provider(settings) if settings.has_openai_key else None
    retriever = build_retriever(
        settings,
        store,
        CachedQueryEmbedder(inner, store.manifest.embedder_provider, store.manifest.embedder_model),
    )
    items = load_golden()
    rows = asyncio.run(run_retrieval(items, retriever, "hybrid_rerank"))
    base = GateConfig.from_dict(settings.load_thresholds())
    before = abstention_metrics(
        [(not r["answerable"], score(r["features"], base) < base.abstain_threshold) for r in rows]
    )
    best = search(rows, base)
    thresholds = {
        "calibrated": True,
        "calibrated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "Written by `python -m eval.calibrate`: grid search maximising gate-level abstention F1 on eval/golden.jsonl with over-refusal <= 5%.",
        "abstain_threshold": best["threshold"],
        "weights": best["weights"],
        "dense_lo": base.dense_lo,
        "dense_hi": base.dense_hi,
        "labels": {"high": base.high, "medium": base.medium},
        "gate_metrics_on_golden": best["metrics"],
        "embed_model": store.manifest.embedder_model,
        "reranker": retriever.reranker.name,
    }
    report = {
        "before": before,
        "after": best["metrics"],
        "chosen": {"weights": best["weights"], "threshold": best["threshold"]},
        "n_items": len(rows),
        "per_item": [{"id": r["id"], "answerable": r["answerable"], "features": r["features"]} for r in rows],
    }
    (ROOT_DIR / "eval" / "results").mkdir(parents=True, exist_ok=True)
    (ROOT_DIR / "eval" / "results" / "calibration.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if not args.dry_run:
        settings.path(settings.thresholds_path).write_text(
            json.dumps(thresholds, indent=2) + "\n", encoding="utf-8"
        )
    sys.stdout.write(
        json.dumps({"before": before, "after": best["metrics"], "chosen": report["chosen"]}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
