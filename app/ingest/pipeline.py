"""Corpus pipeline shared by ingestion and audit: load -> clean -> parse -> chunk -> dedupe."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingest.chunker import Chunker, ChunkerConfig
from app.ingest.cleaner import clean_lines
from app.ingest.dedup import DedupResult, deduplicate
from app.ingest.loaders import load_document
from app.ingest.models import Chunk, RawDocument
from app.ingest.sources import load_sources
from app.ingest.structure import ParsedDocument, parse_structure


@dataclass
class CorpusDocument:
    """One document at every pipeline stage."""

    raw: RawDocument
    parsed: ParsedDocument
    chunks: list[Chunk]


@dataclass
class Corpus:
    """Whole corpus after chunking and de-duplication."""

    documents: list[CorpusDocument]
    dedup: DedupResult

    @property
    def chunks(self) -> list[Chunk]:
        """De-duplicated chunks in stable document order."""
        return self.dedup.chunks


class MissingSourceError(FileNotFoundError):
    """A document listed in sources.yaml is not present in data/raw."""


def load_corpus(data_dir: Path, config: ChunkerConfig | None = None) -> Corpus:
    """Run the full text pipeline over every document in `data/sources.yaml`."""
    metas = load_sources(data_dir / "sources.yaml")
    missing = [m.file for m in metas if not (data_dir / "raw" / m.file).exists()]
    if missing:
        raise MissingSourceError(f"missing source files in {data_dir / 'raw'}: {', '.join(missing)}")
    chunker = Chunker(config)
    documents: list[CorpusDocument] = []
    for meta in metas:
        raw = load_document(data_dir / "raw" / meta.file, meta)
        parsed = parse_structure(meta, clean_lines(raw.lines), raw.tables)
        documents.append(CorpusDocument(raw=raw, parsed=parsed, chunks=chunker.chunk(parsed)))
    all_chunks = [c for d in documents for c in d.chunks]
    return Corpus(documents=documents, dedup=deduplicate(all_chunks))
