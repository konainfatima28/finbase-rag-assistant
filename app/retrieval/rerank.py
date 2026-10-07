"""Pluggable reranker (PROMPT.md §6.5): FlashRank ONNX cross-encoder, with a `none` fallback."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)


class Reranker(Protocol):
    """Scores (query, passage) pairs; higher = more relevant; scores in [0, 1] when available."""

    name: str

    def score(self, query: str, passages: Sequence[str]) -> list[float] | None:
        """Relevance per passage, or None when the reranker is unavailable (caller keeps fused order)."""
        ...


class NoneReranker:
    """No-op reranker (keeps fused order)."""

    name = "none"

    def score(self, query: str, passages: Sequence[str]) -> list[float] | None:
        """Always None."""
        return None


def _bounded_session(session: Any, threads: int) -> Any:
    """Re-create FlashRank's ONNX session with the CPU memory arena and memory patterns disabled.

    Measured: with onnxruntime defaults, RSS grew 159 MB -> 849 MB after 6 rerank calls (variable sequence
    lengths keep allocating arena blocks), which would exceed Render's 512 MB. Same model, same outputs.
    """
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.enable_cpu_mem_arena = False
    options.enable_mem_pattern = False
    if threads > 0:
        options.intra_op_num_threads = threads
    return ort.InferenceSession(session._model_path, sess_options=options, providers=["CPUExecutionProvider"])


class FlashRankReranker:
    """FlashRank cross-encoder (ONNX, no torch). Loads lazily; failure degrades to fused order."""

    name = "flashrank"

    def __init__(self, model_name: str, cache_dir: Path, threads: int = 2) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.threads = threads
        self._ranker: Any = None
        self._failed = False
        self._lock = threading.Lock()

    def load(self) -> bool:
        """Load/warm the model; returns False (and logs) if unavailable."""
        with self._lock:
            if self._ranker is not None or self._failed:
                return self._ranker is not None
            try:
                from flashrank import Ranker  # heavy import kept off the module import path

                ranker = Ranker(model_name=self.model_name, cache_dir=str(self.cache_dir))
                ranker.session = _bounded_session(ranker.session, self.threads)
                self._ranker = ranker
            except Exception as exc:  # network/model download/onnx failures must not take the API down
                self._failed = True
                log.warning("reranker_unavailable", model=self.model_name, error=str(exc))
                return False
            return True

    @property
    def available(self) -> bool:
        """True once loaded successfully."""
        return self._ranker is not None

    def score(self, query: str, passages: Sequence[str]) -> list[float] | None:
        """Cross-encoder scores aligned with `passages`."""
        if not passages or not self.load():
            return None
        from flashrank import RerankRequest

        request = RerankRequest(query=query, passages=[{"id": i, "text": p} for i, p in enumerate(passages)])
        try:
            with self._lock:  # onnxruntime session is shared; keep calls serialised
                results = self._ranker.rerank(request)
        except Exception as exc:
            log.warning("rerank_failed", error=str(exc))
            return None
        scores = [0.0] * len(passages)
        for item in results:
            scores[int(item["id"])] = float(item["score"])
        return scores


def make_reranker(kind: str, model_name: str, cache_dir: Path) -> Reranker:
    """Factory from settings (`RERANKER=flashrank|none`)."""
    if kind == "flashrank":
        return FlashRankReranker(model_name, cache_dir)
    return NoneReranker()
