"""`python -m app.retrieval.warm`: download/warm the FlashRank ONNX model at BUILD time so the API never
downloads it on a cold start (used by render.yaml, backend/Dockerfile and CI)."""

from __future__ import annotations

import sys

from app.retrieval.rerank import FlashRankReranker
from app.settings import get_settings


def main() -> int:
    settings = get_settings()
    if settings.reranker != "flashrank":
        return 0
    reranker = FlashRankReranker(settings.reranker_model, settings.path(settings.cache_dir) / "flashrank")
    ok = reranker.load() and reranker.score("warm up", ["warm up passage"]) is not None
    sys.stderr.write(
        f"flashrank {settings.reranker_model}: {'ready' if ok else 'UNAVAILABLE (API will use fused order)'}\n"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
