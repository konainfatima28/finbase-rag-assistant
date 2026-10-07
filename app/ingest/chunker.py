"""Structure-aware chunker (PROMPT.md §5.3).

Unit = leaf (sub)section. A section whose text + tables fit in `max_tokens` becomes ONE self-contained
chunk (tables linearised inline as `Column: value | ...` rows, header repeated per row); longer sections
are split at line (paragraph/bullet) boundaries with ~12% overlap and each table gets its own chunk(s).
Every table row additionally becomes a short `table_row` fact chunk pointing at its parent chunk, every
FAQ item becomes one `faq` chunk. Every chunk carries the full breadcrumb and a contextual header.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.ingest.models import Chunk, ChunkType, QualityFlag, Table
from app.ingest.structure import FaqItem, ParsedDocument, Section
from app.text.numbers import find_malformed_amounts
from app.text.tokenize import count_tokens

CHUNKER_VERSION = "structure-v1.0"


@dataclass(frozen=True)
class ChunkerConfig:
    """Chunk size parameters (tokens)."""

    max_tokens: int = 600
    target_tokens: int = 400
    overlap_ratio: float = 0.12


def linearize_row(header: list[str], row: list[str]) -> str:
    """'Col: value | Col: value' (empty cells skipped)."""
    return " | ".join(
        f"{h or f'Column {i + 1}'}: {v}" for i, (h, v) in enumerate(zip(header, row, strict=False)) if v
    )


def table_lines(table: Table) -> list[str]:
    """All data rows of a table, linearised with the header repeated on every row."""
    return [linearize_row(table.header, row) for row in table.rows]


def make_chunk_id(doc_id: str, section_id: str, chunk_type: str, text: str, faq_id: str | None = None) -> str:
    """Stable id: sha256 of identity + text (re-ingestion yields identical ids)."""
    key = f"{doc_id}|{section_id}|{chunk_type}|{faq_id or ''}|{text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _pages_label(start: int, end: int) -> str:
    return f"p.{start}" if start == end else f"p.{start}-{end}"


class Chunker:
    """Turns a `ParsedDocument` into chunks."""

    def __init__(self, config: ChunkerConfig | None = None) -> None:
        self.config = config or ChunkerConfig()

    # --- public ----------------------------------------------------------------------------------
    def chunk(self, doc: ParsedDocument) -> list[Chunk]:
        """Chunk every section of the document, in document order."""
        chunks: list[Chunk] = []
        for section in doc.sections.values():
            if section.kind == "faq":
                chunks.extend(self._faq_chunk(doc, section, item) for item in doc.faqs)
            elif section.lines:
                chunks.extend(self._section_chunks(doc, section))
        return chunks

    # --- helpers ---------------------------------------------------------------------------------
    def _header(self, doc: ParsedDocument, section: Section, start: int, end: int, suffix: str = "") -> str:
        label = f"Section {section.section_id} {section.title}{suffix}"
        return f"[{doc.meta.title} | {doc.meta.code} | {label} | {_pages_label(start, end)}]"

    def _make(
        self,
        doc: ParsedDocument,
        section: Section,
        chunk_type: ChunkType,
        text: str,
        start: int,
        end: int,
        *,
        faq: FaqItem | None = None,
        parent_chunk_id: str | None = None,
        boilerplate: bool = False,
    ) -> Chunk:
        suffix = f" › {faq.faq_id}" if faq else ""
        header = self._header(doc, section, start, end, suffix)
        flag: QualityFlag = "suspect_value" if find_malformed_amounts(text) else "ok"
        return Chunk(
            chunk_id=make_chunk_id(
                doc.meta.doc_id, section.section_id, chunk_type, text, faq.faq_id if faq else None
            ),
            doc_id=doc.meta.doc_id,
            doc_title=doc.meta.title,
            doc_code=doc.meta.code,
            effective_date=doc.meta.effective_date,
            section_id=section.section_id,
            section_title=section.title,
            breadcrumb=doc.breadcrumb(section.section_id) + (f" › {faq.faq_id}" if faq else ""),
            page_start=start,
            page_end=end,
            chunk_type=chunk_type,
            text=text,
            header=header,
            faq_id=faq.faq_id if faq else None,
            question=faq.question_core if faq else None,
            clause_ref=faq.clause_ref if faq else None,
            parent_chunk_id=parent_chunk_id,
            quality_flag=flag,
            token_count=count_tokens(f"{header}\n{text}"),
            text_section_refs=faq.text_section_refs if faq else [],
            boilerplate=boilerplate,
        )

    def _faq_chunk(self, doc: ParsedDocument, section: Section, item: FaqItem) -> Chunk:
        return self._make(doc, section, "faq", item.text, item.page_start, item.page_end, faq=item)

    def _section_chunks(self, doc: ParsedDocument, section: Section) -> list[Chunk]:
        boilerplate = section.templated_title
        base_type: ChunkType = (
            "boilerplate" if boilerplate else ("annex" if section.kind == "annex" else "policy")
        )
        # Render lines: text lines as-is, table placeholders as linearised rows.
        rendered: list[tuple[str, int, int, str | None]] = []  # (text, page_start, page_end, table_id)
        for line in section.lines:
            if line.table_id:
                table = doc.tables[line.table_id]
                rendered.append(
                    ("\n".join(table_lines(table)), table.page_start, table.page_end, table.table_id)
                )
            else:
                rendered.append((line.text, line.page, line.page, None))
        total = count_tokens("\n".join(r[0] for r in rendered))
        has_table = any(r[3] for r in rendered)
        chunks: list[Chunk] = []
        table_parent: dict[str, str] = {}

        if total <= self.config.max_tokens:
            text = "\n".join(r[0] for r in rendered)
            table_tokens = sum(count_tokens(r[0]) for r in rendered if r[3])
            ctype: ChunkType = base_type
            if has_table and not boilerplate and base_type == "policy" and table_tokens * 2 > total:
                ctype = "table"
            chunk = self._make(
                doc,
                section,
                ctype,
                text,
                min(r[1] for r in rendered),
                max(r[2] for r in rendered),
                boilerplate=boilerplate,
            )
            chunks.append(chunk)
            table_parent.update({r[3]: chunk.chunk_id for r in rendered if r[3]})
        else:
            text_lines = [r for r in rendered if not r[3]]
            for group in self._split(text_lines):
                text = "\n".join(r[0] for r in group)
                chunks.append(
                    self._make(
                        doc,
                        section,
                        base_type,
                        text,
                        group[0][1],
                        max(r[2] for r in group),
                        boilerplate=boilerplate,
                    )
                )
            for idx, r in enumerate(rendered):
                if not r[3]:
                    continue
                intro = (
                    rendered[idx - 1][0]
                    if idx > 0 and not rendered[idx - 1][3] and rendered[idx - 1][0].endswith(":")
                    else ""
                )
                table = doc.tables[r[3]]
                rows: list[tuple[str, int, int, str | None]] = [
                    (row_text, table.page_start, table.page_end, None) for row_text in table_lines(table)
                ]
                for n, group in enumerate(self._split(rows)):
                    body = "\n".join(g[0] for g in group)
                    text = f"{intro}\n{body}" if intro and n == 0 else body
                    chunk = self._make(
                        doc, section, "table", text, table.page_start, table.page_end, boilerplate=boilerplate
                    )
                    chunks.append(chunk)
                    table_parent.setdefault(table.table_id, chunk.chunk_id)

        for table_id, parent_id in table_parent.items():
            table = doc.tables[table_id]
            for row in table.rows:
                fact = f"{section.title} — {linearize_row(table.header, row)}"
                chunks.append(
                    self._make(
                        doc,
                        section,
                        "table_row",
                        fact,
                        table.page_start,
                        table.page_end,
                        parent_chunk_id=parent_id,
                        boilerplate=boilerplate,
                    )
                )
        return chunks

    def _split(
        self, items: list[tuple[str, int, int, str | None]]
    ) -> list[list[tuple[str, int, int, str | None]]]:
        """Greedy packing at line boundaries up to target size, with ~overlap_ratio carry-over."""
        groups: list[list[tuple[str, int, int, str | None]]] = []
        current: list[tuple[str, int, int, str | None]] = []
        size = 0
        overlap_budget = int(self.config.target_tokens * self.config.overlap_ratio)
        for item in items:
            tokens = count_tokens(item[0])
            if current and size + tokens > self.config.target_tokens:
                groups.append(current)
                carry: list[tuple[str, int, int, str | None]] = []
                carry_size = 0
                for prev in reversed(current):
                    prev_tokens = count_tokens(prev[0])
                    if carry_size + prev_tokens > overlap_budget:
                        break
                    carry.insert(0, prev)
                    carry_size += prev_tokens
                current, size = carry, carry_size
            current.append(item)
            size += tokens
        if current:
            groups.append(current)
        return groups
