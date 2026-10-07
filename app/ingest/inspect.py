"""Print chunks for manual inspection: `python -m app.ingest.inspect [--sample] [--id CHUNK_ID] [--grep TEXT]`.

Reads `data/processed/chunks.jsonl` (written by `python -m app.ingest`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.settings import get_settings

#: (doc_id, predicate description) pairs giving a representative sample across chunk types
SAMPLE_PICKS: list[tuple[str, str, str]] = [
    ("personal_loans", "section", "6.2"),
    ("personal_loans", "table_row", "Loan Cancellation"),
    ("credit_cards", "section", "1.2"),
    ("credit_cards", "table_row", "Late Payment Fee (₹501"),
    ("payments_upi", "faq", "Q001"),
    ("payments_upi", "boilerplate", ""),
    ("savings_account", "table", "2"),
    ("fd_wealth", "section", "22"),
    ("kyc_security", "section", "5"),
    ("kyc_security", "annex", ""),
]


def _pick(chunks: list[dict[str, object]], doc_id: str, kind: str, key: str) -> dict[str, object] | None:
    for chunk in chunks:
        if chunk["doc_id"] != doc_id:
            continue
        if kind == "section" and chunk["section_id"] == key and chunk["chunk_type"] in ("policy", "table"):
            return chunk
        if kind == "faq" and chunk["faq_id"] == key:
            return chunk
        if kind == "table" and chunk["chunk_type"] == "table" and chunk["section_id"] == key:
            return chunk
        if (
            kind in ("table_row", "boilerplate", "annex")
            and chunk["chunk_type"] == kind
            and key in str(chunk["text"])
        ):
            return chunk
    return None


def render(chunk: dict[str, object], width: int = 700) -> str:
    """Compact human-readable view of a chunk."""
    meta = (
        f"id={chunk['chunk_id']} type={chunk['chunk_type']} doc={chunk['doc_id']} section={chunk['section_id']} "
        f"pages={chunk['page_start']}-{chunk['page_end']} tokens={chunk['token_count']} flag={chunk['quality_flag']}"
    )
    extra = []
    if chunk.get("faq_id"):
        extra.append(
            f"faq={chunk['faq_id']} clause={chunk['clause_ref']} text_section_refs={chunk['text_section_refs']}"
        )
    if chunk.get("parent_chunk_id"):
        extra.append(f"parent={chunk['parent_chunk_id']}")
    dups = [str(d) for d in chunk.get("source_duplicates") or []]  # type: ignore[attr-defined]
    if dups:
        extra.append(f"duplicates={len(dups)} ({', '.join(dups[:3])}…)")
    text = str(chunk["text"])
    body = text if len(text) <= width else text[:width] + " …"
    return "\n".join([meta, *extra, f"breadcrumb: {chunk['breadcrumb']}", str(chunk["header"]), body])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Inspect processed chunks")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--id")
    parser.add_argument("--grep")
    args = parser.parse_args(argv)
    path: Path = get_settings().path(get_settings().data_dir) / "processed" / "chunks.jsonl"
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected: list[dict[str, object]] = []
    if args.sample:
        selected = [c for pick in SAMPLE_PICKS if (c := _pick(chunks, *pick)) is not None]
    if args.id:
        selected += [c for c in chunks if c["chunk_id"] == args.id]
    if args.grep:
        selected += [c for c in chunks if args.grep.lower() in str(c["text"]).lower()]
    for chunk in selected:
        sys.stdout.write(render(chunk) + "\n" + "-" * 100 + "\n")
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
