"""Index-level ablations (PROMPT.md §11.2): structure-aware vs fixed-size chunking, with/without dedup,
with/without contextual headers.

  python -m eval.ablations                 # BM25 for every variant (offline, no OpenAI calls)
  python -m eval.ablations --dense         # + dense and hybrid+rerank per variant (needs OPENAI_API_KEY;
                                           #   embeddings cached in .cache/embeddings.sqlite)
Writes eval/results/ablations.json (merged into the eval report / dashboard).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ingest.__main__ import conflict_groups
from app.ingest.build import build_index
from app.ingest.chunker import make_chunk_id, table_lines
from app.ingest.models import Chunk
from app.ingest.pipeline import Corpus, load_corpus
from app.providers.factory import get_embedding_provider
from app.retrieval.bm25 import BM25
from app.retrieval.pipeline import preprocess
from app.retrieval.store import IndexStore, lexical_text
from app.safety.pii import redact
from app.settings import ROOT_DIR, get_settings
from app.text.tokenize import count_tokens, tokenize
from eval.golden import load_golden
from eval.metrics import mean, retrieval_metrics
from eval.run import CachedQueryEmbedder, build_retriever, run_retrieval, summarize_retrieval

FIXED_TOKENS = 300
FIXED_OVERLAP = 0.15


def no_headers(chunks: list[Chunk]) -> list[Chunk]:
    """Same chunks, contextual header removed from the embedded/indexed text."""
    return [c.model_copy(update={"header": ""}) for c in chunks]


def fixed_size_chunks(corpus: Corpus) -> list[Chunk]:
    """Naive baseline: ~300-token windows with 15% overlap over each document's cleaned text, ignoring
    structure (no dedup, no headers). Each window is labelled with its majority section (for scoring)."""
    out: list[Chunk] = []
    for cdoc in corpus.documents:
        doc = cdoc.parsed
        lines: list[tuple[str, str, int, str | None]] = []  # (text, section_id, page, faq_id)
        for section in doc.sections.values():
            for line in section.lines:
                if line.table_id:
                    for row in table_lines(doc.tables[line.table_id]):
                        lines.append((row, section.section_id, line.page, None))
                else:
                    faq = (
                        line.text[:4]
                        if section.kind == "faq" and line.text[:1] == "Q" and line.text[4:5] == ":"
                        else None
                    )
                    lines.append((line.text, section.section_id, line.page, faq))
        start = 0
        while start < len(lines):
            size, end = 0, start
            while end < len(lines) and size < FIXED_TOKENS:
                size += count_tokens(lines[end][0])
                end += 1
            window = lines[start:end]
            majority = Counter(w[1] for w in window).most_common(1)[0][0]
            faq = next((w[3] for w in window if w[3]), None)
            text = "\n".join(w[0] for w in window)
            out.append(
                Chunk(
                    chunk_id=make_chunk_id(doc.meta.doc_id, f"fixed{start}", "policy", text),
                    doc_id=doc.meta.doc_id,
                    doc_title=doc.meta.title,
                    doc_code=doc.meta.code,
                    effective_date=doc.meta.effective_date,
                    section_id=majority,
                    section_title="",
                    breadcrumb=doc.meta.title,
                    page_start=window[0][2],
                    page_end=window[-1][2],
                    chunk_type="faq" if faq and majority == "23" else "policy",
                    faq_id=faq if majority == "23" else None,
                    text=text,
                    header="",
                    token_count=size,
                )
            )
            if end >= len(lines):
                break
            back = 0
            overlap = 0
            while back < end - start - 1 and overlap < FIXED_TOKENS * FIXED_OVERLAP:
                overlap += count_tokens(lines[end - 1 - back][0])
                back += 1
            start = end - back
    return out


def variants(corpus: Corpus) -> dict[str, list[Chunk]]:
    """Chunk lists per ablation variant (fixed order)."""
    raw = [c for d in corpus.documents for c in d.chunks]
    return {
        "structure+dedup+headers (default)": corpus.chunks,
        "no dedup": raw,
        "no contextual headers": no_headers(corpus.chunks),
        "fixed-size chunks (baseline)": fixed_size_chunks(corpus),
    }


def bm25_eval(chunks: list[Chunk], items: list[dict[str, Any]], final_k: int = 5) -> dict[str, float | None]:
    """BM25-only retrieval metrics for a chunk list (offline)."""
    bm25 = BM25.build([tokenize(lexical_text(c)) for c in chunks])
    rows = []
    for item in items:
        if not item["answerable"] or not item.get("gold_sections"):
            continue
        query = preprocess(redact(item.get("retrieval_query") or item["question"]).text)
        ranked = [chunks[i] for i, _ in bm25.top_k(tokenize(query), 20)]
        rows.append(retrieval_metrics(item, ranked, ranked[:final_k]))
    keys = ("hit@1", "recall@5", "mrr", "ndcg@10", "context_precision")
    return {k: mean(r[k] for r in rows if r) for k in keys} | {"n": float(len(rows))}


def dense_eval(
    chunks: list[Chunk], items: list[dict[str, Any]], corpus: Corpus
) -> dict[str, dict[str, float | None]]:
    """Dense + hybrid+rerank metrics for a chunk list via a temporary real (OpenAI) index."""
    settings = get_settings()
    with tempfile.TemporaryDirectory() as tmp:
        local = settings.model_copy(update={"index_root": Path(tmp)})
        build_index(chunks, local, provider="openai", conflicts=conflict_groups(corpus))  # sync (own loop)
        store = IndexStore.load(local.index_dir, local)
        embedder = CachedQueryEmbedder(
            get_embedding_provider(settings), store.manifest.embedder_provider, store.manifest.embedder_model
        )
        retriever = build_retriever(local, store, embedder)

        async def score() -> dict[str, dict[str, float | None]]:
            out: dict[str, dict[str, float | None]] = {}
            for mode in ("dense", "hybrid_rerank"):
                rows = await run_retrieval(items, retriever, mode)
                out[mode] = {
                    k: v
                    for k, v in summarize_retrieval(rows).items()
                    if k in ("hit@1", "recall@5", "mrr", "ndcg@10")
                }
            return out

        return asyncio.run(score())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunking / dedup / header ablations")
    parser.add_argument(
        "--dense", action="store_true", help="also run dense + hybrid_rerank (needs OPENAI_API_KEY)"
    )
    args = parser.parse_args(argv)
    corpus = load_corpus(ROOT_DIR / "data")
    items = load_golden()
    rows = []
    for name, chunks in variants(corpus).items():
        row: dict[str, Any] = {"variant": name, "chunks": len(chunks), "bm25": bm25_eval(chunks, items)}
        if args.dense:
            row.update(dense_eval(chunks, items, corpus))
        rows.append(row)
    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dense": args.dense,
        "rows": rows,
    }
    out = ROOT_DIR / "eval" / "results" / "ablations.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    sys.stdout.write(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
