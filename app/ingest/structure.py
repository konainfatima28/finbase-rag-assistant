"""Section-tree parser (PROMPT.md §3.2.8) and FAQ item parser.

Input: cleaned, page-tagged lines. Output: document front matter, the Table of Contents (titles learnt,
then stripped from content), an ordered section tree with page spans, and the FAQ items.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingest.models import DocMeta, Line, Table

TOC_ENTRY = re.compile(r"^•?\s*\[Section (?P<id>[\w.-]+): (?P<title>.+?)\]\(#[\w.-]+\)$")
TOP_HEADING = re.compile(r"^Section (?P<id>\d+): (?P<title>.+)$")
SUB_HEADING_EXPLICIT = re.compile(r"^Section (?P<parent>\d+)\.(?P<sub>\d+): (?P<title>.+)$")
SUB_HEADING_NUMERIC = re.compile(r"^(?P<parent>\d{1,2})\.(?P<sub>\d{1,2}) (?P<title>[A-Z][^.]{1,90})$")
ANNEX_HEADING = re.compile(r"^Section (?P<id>[A-Z]{2,5}-EXT-\d{3}): (?P<title>.+)$")
FAQ_START = re.compile(r"^Q(?P<num>\d{3}): (?P<rest>.*)$")
FAQ_ANYWHERE = re.compile(r"(?<![A-Za-z0-9])Q(\d{3}):")
CLAUSE_REF = re.compile(r"\[Reference[^\]]*?Clause\s+(?P<clause>[A-Z0-9-]+)\]")
TEXT_SECTION_REF = re.compile(r"\bSection (?P<ref>\d+(?:\.\d+)?)\b")
COUNTER_SUFFIX = re.compile(r"\s*\((?:[A-Za-z ]*?)(?:case|inquiry|query)\s+\d+\)\s*$", re.I)

FRONT_MATTER_ID = "0"


@dataclass
class Section:
    """A (sub)section with its own content lines (headings excluded)."""

    section_id: str
    title: str
    level: int
    parent_id: str | None
    page_start: int
    page_end: int
    kind: str = "content"  # content | faq | annex | front_matter
    templated_title: bool = False  # heading ends with its own number ("... Standards 7")
    lines: list[Line] = field(default_factory=list)
    children: list[str] = field(default_factory=list)

    def extend_pages(self, page: int) -> None:
        """Grow the page span to include `page`."""
        self.page_start = min(self.page_start, page)
        self.page_end = max(self.page_end, page)


@dataclass
class FaqItem:
    """One Q&A from the FAQ directory."""

    faq_id: str
    number: int
    question: str
    question_core: str
    answer: str
    bullets: list[str]
    page_start: int
    page_end: int
    clause_ref: str | None
    text_section_refs: list[str]

    @property
    def text(self) -> str:
        """Full Q&A text (question, answer, attribute bullets)."""
        parts = [f"{self.faq_id}: {self.question}", self.answer, *self.bullets]
        return "\n".join(p for p in parts if p)


@dataclass
class ParsedDocument:
    """Structure of one document."""

    meta: DocMeta
    toc: dict[str, str]
    sections: dict[str, Section]
    faqs: list[FaqItem]
    tables: dict[str, Table]

    def breadcrumb(self, section_id: str) -> str:
        """'Doc title › Section 6 Title › 6.2 Title'."""
        chain: list[Section] = []
        current: str | None = section_id
        while current is not None:
            section = self.sections[current]
            chain.append(section)
            current = section.parent_id
        labels = [
            f"Section {s.section_id} {s.title}" if s.level == 1 else f"{s.section_id} {s.title}"
            for s in reversed(chain)
        ]
        return " › ".join([self.meta.title, *labels])


def _strip_templated_number(section_id: str, title: str) -> tuple[str, bool]:
    match = re.match(rf"^(?P<t>.*\S)\s+{re.escape(section_id)}$", title)
    return (match.group("t"), True) if match else (title, False)


def parse_structure(meta: DocMeta, lines: list[Line], tables: dict[str, Table]) -> ParsedDocument:
    """Build the section tree from cleaned lines."""
    toc: dict[str, str] = {}
    sections: dict[str, Section] = {}
    first_page = lines[0].page if lines else 1
    front = Section(
        FRONT_MATTER_ID, "Document Information", 1, None, first_page, first_page, kind="front_matter"
    )
    sections[front.section_id] = front
    current = front
    top: Section | None = None
    in_toc = False

    def open_section(section: Section) -> Section:
        if section.section_id in sections:  # duplicated heading id: keep first, suffix the repeat
            section.section_id = (
                f"{section.section_id}#dup{sum(1 for k in sections if k.startswith(section.section_id))}"
            )
        if section.parent_id and section.parent_id not in sections:
            # orphan sub-section (parent heading absent): never invent a parent, treat it as top-level
            section.parent_id, section.level = None, 1
        sections[section.section_id] = section
        if section.parent_id:
            sections[section.parent_id].children.append(section.section_id)
        return section

    for line in lines:
        text = line.text
        if text == "Table of Contents":
            in_toc = True
            continue
        toc_match = TOC_ENTRY.match(text)
        if toc_match:
            toc[toc_match.group("id")] = toc_match.group("title")
            continue
        if in_toc and not line.table_id:
            in_toc = False
        if line.table_id is None:
            heading = _match_heading(text, top)
            if heading is not None:
                sid, title, level, parent, kind = heading
                title, templated = _strip_templated_number(sid, title)
                section = Section(
                    sid, title, level, parent, line.page, line.page, kind=kind, templated_title=templated
                )
                current = open_section(section)
                if level == 1:
                    top = current
                continue
        current.lines.append(line)
        current.extend_pages(line.page)
        if current.parent_id:
            sections[current.parent_id].extend_pages(line.page)

    faqs: list[FaqItem] = []
    for section in sections.values():
        if section.kind == "faq":
            faqs.extend(parse_faqs(section.lines))
    return ParsedDocument(meta=meta, toc=toc, sections=sections, faqs=faqs, tables=tables)


def _match_heading(text: str, top: Section | None) -> tuple[str, str, int, str | None, str] | None:
    match = ANNEX_HEADING.match(text)
    if match:
        return match.group("id"), match.group("title"), 1, None, "annex"
    match = SUB_HEADING_EXPLICIT.match(text)
    if match:
        sid = f"{match.group('parent')}.{match.group('sub')}"
        return sid, match.group("title"), 2, match.group("parent"), "content"
    match = TOP_HEADING.match(text)
    if match:
        kind = "faq" if "faq" in match.group("title").lower() else "content"
        return match.group("id"), match.group("title"), 1, None, kind
    match = SUB_HEADING_NUMERIC.match(text)
    if match and top is not None and top.kind == "content" and match.group("parent") == top.section_id:
        sid = f"{match.group('parent')}.{match.group('sub')}"
        return sid, match.group("title"), 2, top.section_id, "content"
    return None


def parse_faqs(lines: list[Line]) -> list[FaqItem]:
    """Split FAQ-directory lines into Q&A items (question line, answer lines, attribute bullets)."""
    items: list[FaqItem] = []
    block: list[Line] = []

    def flush() -> None:
        if not block:
            return
        head = FAQ_START.match(block[0].text)
        if head is None:
            return
        question = head.group("rest").strip()
        answer_parts = [ln.text for ln in block[1:] if not ln.text.startswith("•")]
        bullets = [ln.text for ln in block[1:] if ln.text.startswith("•")]
        answer = " ".join(answer_parts).strip()
        clause = CLAUSE_REF.search(answer)
        items.append(
            FaqItem(
                faq_id=f"Q{head.group('num')}",
                number=int(head.group("num")),
                question=question,
                question_core=COUNTER_SUFFIX.sub("", question).strip(),
                answer=answer,
                bullets=bullets,
                page_start=block[0].page,
                page_end=block[-1].page,
                clause_ref=clause.group("clause") if clause else None,
                text_section_refs=TEXT_SECTION_REF.findall(answer),
            )
        )

    for line in lines:
        if FAQ_START.match(line.text):
            flush()
            block = [line]
        elif block:
            block.append(line)
    flush()
    return items
