"""Data models shared by loaders, cleaner, structure parser, chunker and the index."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ChunkType = Literal["policy", "table", "table_row", "faq", "boilerplate", "annex"]
QualityFlag = Literal["ok", "suspect_value"]


class DocMeta(BaseModel):
    """Stable document identity from `data/sources.yaml`."""

    doc_id: str
    title: str
    code: str
    effective_date: str
    file: str


@dataclass
class Line:
    """One logical line of extracted text with provenance.

    `wrapped` is a geometric hint from the PDF loader: the line reaches the right margin, so the next
    line is probably a soft-wrap continuation; `bold` is the font weight (a style change is a block
    boundary, e.g. bold FAQ question vs regular answer). `table_id` marks a placeholder line standing for a table.
    """

    text: str
    page: int
    wrapped: bool | None = None
    table_id: str | None = None
    bold: bool | None = None


@dataclass
class Table:
    """A table with a header row and data rows (cells already cleaned)."""

    table_id: str
    page_start: int
    page_end: int
    header: list[str]
    rows: list[list[str]]
    merged_cells: list[str] = field(default_factory=list)  # cells that spanned several lines in the source


@dataclass
class Page:
    """Per-page raw extraction (kept for audit and citations)."""

    number: int
    raw_text: str


@dataclass
class RawDocument:
    """Loader output common to every file type."""

    meta: DocMeta
    source_path: Path
    sha256: str
    pages: list[Page]
    lines: list[Line]
    tables: dict[str, Table] = field(default_factory=dict)


class Chunk(BaseModel):
    """A retrievable unit with complete structural metadata (PROMPT.md §5.3)."""

    chunk_id: str
    doc_id: str
    doc_title: str
    doc_code: str
    effective_date: str
    section_id: str
    section_title: str
    breadcrumb: str
    page_start: int
    page_end: int
    chunk_type: ChunkType
    text: str
    header: str
    faq_id: str | None = None
    question: str | None = None
    clause_ref: str | None = None
    parent_chunk_id: str | None = None
    quality_flag: QualityFlag = "ok"
    token_count: int = 0
    boilerplate: bool = False
    text_section_refs: list[str] = Field(default_factory=list)
    source_duplicates: list[str] = Field(default_factory=list)

    @property
    def embed_text(self) -> str:
        """Text that is embedded and shown to the LLM: contextual header + body."""
        return f"{self.header}\n{self.text}"

    @property
    def page_label(self) -> str:
        """'p. 9' or 'pp. 9-10'."""
        if self.page_end != self.page_start:
            return f"pp. {self.page_start}-{self.page_end}"
        return f"p. {self.page_start}"

    @property
    def citation_label(self) -> str:
        """Structural citation, always paired with the document title."""
        if self.chunk_type == "faq" and self.faq_id:
            return f"{self.doc_title} — FAQ {self.faq_id} ({self.page_label})"
        return f"{self.doc_title} — Section {self.section_id} ({self.page_label})"
