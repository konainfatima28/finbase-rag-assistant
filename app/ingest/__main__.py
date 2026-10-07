"""Ingestion CLI: `python -m app.ingest [--embedder openai] [--rebuild] [--chunks-only]`.

Stages: load -> clean -> parse -> chunk -> dedupe (always) and, unless --chunks-only, embed + build the
FAISS/BM25 index into `indexes/<provider>-<model>/` with a manifest. Idempotent: embeddings are cached
on disk, so re-running only embeds new/changed chunk texts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.audit import audit_structure, detect_conflicts
from app.ingest.build import build_index
from app.ingest.models import Chunk
from app.ingest.pipeline import Corpus, load_corpus
from app.settings import Settings, get_settings


def write_processed(corpus: Corpus, processed_dir: Path) -> None:
    """Write the de-duplicated chunks and the dedup report."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    write_chunks_jsonl(corpus.chunks, processed_dir / "chunks.jsonl")
    (processed_dir / "dedup_report.json").write_text(
        json.dumps(corpus.dedup.report, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_chunks_jsonl(chunks: list[Chunk], path: Path) -> None:
    """One JSON object per line, stable key order (byte-identical across runs)."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")


def conflict_groups(corpus: Corpus) -> list[dict[str, Any]]:
    """Multi-member conflicts detected in the parsed documents (deduplicated), for context assembly."""
    groups: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for cdoc in corpus.documents:
        for finding in detect_conflicts(cdoc.parsed):
            key = (finding.doc_id, tuple(finding.members))
            if not finding.members or key in seen:
                continue
            seen.add(key)
            groups.append(
                {
                    "doc_id": finding.doc_id,
                    "category": finding.category,
                    "members": finding.members,
                    "detail": finding.detail,
                }
            )
    return groups


def missing_sections(corpus: Corpus) -> list[dict[str, str]]:
    """Sections listed in a TOC but absent from the body (from the audit's structure check)."""
    out = []
    for cdoc in corpus.documents:
        meta = cdoc.parsed.meta
        for item in audit_structure(cdoc)["missing_in_body"]:
            out.append(
                {
                    "doc_id": meta.doc_id,
                    "doc_title": meta.title,
                    "section_id": item["section"],
                    "title": item["toc_title"],
                }
            )
    return out


def summarize(corpus: Corpus) -> dict[str, Any]:
    """Counts for the CLI summary."""
    by_type = Counter(c.chunk_type for c in corpus.chunks)
    return {
        "documents": len(corpus.documents),
        "pages": sum(len(d.raw.pages) for d in corpus.documents),
        "sections": sum(
            len([s for s in d.parsed.sections.values() if s.kind != "front_matter"]) for d in corpus.documents
        ),
        "faq_items_parsed": sum(len(d.parsed.faqs) for d in corpus.documents),
        "chunks_before_dedup": corpus.dedup.report["input_chunks"],
        "duplicates_removed": corpus.dedup.report["removed"],
        "chunks": len(corpus.chunks),
        "chunks_by_type": dict(sorted(by_type.items())),
        "suspect_value_chunks": sum(1 for c in corpus.chunks if c.quality_flag == "suspect_value"),
        "tokens": sum(c.token_count for c in corpus.chunks),
    }


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    """CLI entry point."""
    settings = settings or get_settings()
    parser = argparse.ArgumentParser(description="Build FinBase chunks and retrieval index")
    parser.add_argument("--embedder", choices=["openai"], default=settings.effective_embed_provider)
    parser.add_argument(
        "--rebuild", action="store_true", help="ignore the embedding cache and rebuild the index"
    )
    parser.add_argument(
        "--chunks-only", action="store_true", help="stop after chunking/dedup (no embeddings)"
    )
    args = parser.parse_args(argv)

    data_dir = settings.path(settings.data_dir)
    corpus = load_corpus(data_dir)
    write_processed(corpus, data_dir / "processed")
    summary: dict[str, Any] = summarize(corpus)
    if not args.chunks_only:
        conflicts = conflict_groups(corpus)
        summary["conflict_groups"] = len(conflicts)
        summary["index"] = build_index(
            corpus.chunks,
            settings,
            provider=args.embedder,
            rebuild=args.rebuild,
            conflicts=conflicts,
            missing_sections=missing_sections(corpus),
        )
    sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
