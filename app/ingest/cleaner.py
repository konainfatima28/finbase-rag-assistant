"""Text cleaning for every extraction trap in PROMPT.md §3.2.

Each trap is a small pure function (unit-tested individually); `clean_lines` applies them in order on
page-tagged lines so page provenance survives cleaning. Values are never "repaired": a truncated amount
such as "₹5,00,0" stays exactly as written and is flagged downstream.
"""

from __future__ import annotations

import re
import unicodedata

from app.ingest.models import Line

# --- trap 1: currency glyph -------------------------------------------------------------------------
_GLYPH = "■"  # U+25A0: how the ZapfDingbats rupee glyph is extracted


def fix_currency_glyph(text: str) -> str:
    """'■' -> '₹' only before a digit, '(' or space+digit; any other '■' is dropped."""
    text = re.sub(rf"{_GLYPH}(?=\d)", "₹", text)
    text = re.sub(rf"{_GLYPH}(?=\()", "₹", text)
    text = re.sub(rf"{_GLYPH} (?=\d)", "₹", text)
    return text.replace(_GLYPH, "")


# --- trap 2: glued lines --------------------------------------------------------------------------------
_SECTION_HEAD = r"Section (?:\d+(?:\.\d+)?|[A-Z]{2,5}-EXT-\d{3}): "
_ROW_ID = r"(?:PAY-ERR|SAV-OPS|CC-SEC|MET-PL|FB-)[A-Z0-9-]*-\d+"


def deglue(text: str) -> str:
    """Split content glued across page breaks / missing newlines (form feeds, headings, FAQ ids, bullets)."""
    text = text.replace("\f", "\n").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(rf"([^\n\[])[ \t]*(?={_SECTION_HEAD})", r"\1\n", text)  # not inside TOC "[Section N: ...]"
    text = re.sub(r"([^\n])[ \t]*(?<![A-Za-z])(?=Q\d{3}: )", r"\1\n", text)
    text = re.sub(r"([^\n])[ \t]*(?=•)", r"\1\n", text)
    text = re.sub(rf"(?<=[a-z0-9)\].])(?={_ROW_ID})", "\n", text)
    text = re.sub(r"(?<=[a-z)\]])(?=\d{1,2}\.\d{1,2} [A-Z][a-z])", "\n", text)
    text = re.sub(r"(?<=[a-z]\.)(?=\d{1,2}\.\d{1,2} [A-Z][a-z])", "\n", text)
    return text


# --- trap 3: soft-wrapped lines -------------------------------------------------------------------
BLOCK_START = re.compile(
    rf"^(?:•|{_SECTION_HEAD}|Q\d{{3}}: |\d{{1,2}}\.\d{{1,2}}:? [A-Z]|\d{{1,2}}\. [A-Z]|⟦TABLE:|{_ROW_ID}\b|Table of Contents)"
)
HEADING = re.compile(rf"^(?:{_SECTION_HEAD}.+|\d{{1,2}}\.\d{{1,2}} [A-Z][^.]{{0,90}})$")
_TERMINAL = (".", ":", "?", "!", ")", "]", "|")
_CONNECTOR_END = re.compile(
    r"\b(?:of|and|or|the|to|a|an|in|for|with|at|by|on|than|per|from|via|under|is|are)$", re.I
)


def _is_continuation(prev: Line, cur: Line) -> bool:
    if prev.table_id or cur.table_id or not prev.text or not cur.text:
        return False
    if BLOCK_START.match(cur.text) or HEADING.match(prev.text):
        return False
    if prev.bold is not None and cur.bold is not None and prev.bold != cur.bold:
        return False
    if prev.wrapped is not None:
        if prev.wrapped:
            return True
        # short line without terminal punctuation followed by lower-case text is still a wrap
        return not prev.text.endswith(_TERMINAL) and cur.text[:1].islower()
    if prev.text.endswith(_TERMINAL):
        return False
    first = cur.text[:1]
    return first.islower() or first.isdigit() or first in "(₹" or bool(_CONNECTOR_END.search(prev.text))


def unwrap(lines: list[Line]) -> list[Line]:
    """Re-join soft-wrapped lines inside paragraphs/bullets/questions (keeps first line's page)."""
    out: list[Line] = []
    for line in lines:
        if out and _is_continuation(out[-1], line):
            prev = out[-1]
            joiner = "" if prev.text.endswith(("-", "/")) and not prev.text.endswith(" -") else " "
            out[-1] = Line(
                text=f"{prev.text}{joiner}{line.text}", page=prev.page, wrapped=line.wrapped, bold=prev.bold
            )
        else:
            out.append(line)
    return out


# --- trap 4: markdown markers ---------------------------------------------------------------------
def strip_markdown(text: str) -> str:
    """Remove **bold** markers and `code` backticks but keep the words."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = text.replace("**", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.replace("`", "")


# --- normalisation --------------------------------------------------------------------------------
def normalize_unicode(text: str) -> str:
    """NFC, unify exotic spaces/dashes, collapse runs of spaces (newlines preserved)."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ").replace(" ", " ").replace("\u200b", "")
    text = text.replace("–", "-").replace("−", "-")
    text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(part.strip() for part in text.split("\n"))


def clean_cell(text: str) -> str:
    """Clean a table cell: glyph, markdown, multi-line cell merged into one line."""
    text = fix_currency_glyph(normalize_unicode(text))
    text = strip_markdown(text)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def clean_lines(lines: list[Line]) -> list[Line]:
    """Apply traps 1-4 to page-tagged lines and return the cleaned logical lines."""
    split: list[Line] = []
    for line in lines:
        if line.table_id:
            split.append(line)
            continue
        text = fix_currency_glyph(normalize_unicode(line.text))
        parts = [p.strip() for p in deglue(text).split("\n")]
        parts = [p for p in parts if p]
        for i, part in enumerate(parts):
            wrapped = line.wrapped if i == len(parts) - 1 else False
            split.append(Line(text=part, page=line.page, wrapped=wrapped, bold=line.bold))
    joined = unwrap(split)
    cleaned: list[Line] = []
    for line in joined:
        if line.table_id:
            cleaned.append(line)
            continue
        text = re.sub(r"[ \t]+", " ", strip_markdown(line.text)).strip()
        if text:
            cleaned.append(Line(text=text, page=line.page, wrapped=line.wrapped, bold=line.bold))
    return cleaned


def clean_text(text: str) -> str:
    """String convenience wrapper over `clean_lines` (page info discarded)."""
    lines = [Line(text=t, page=1) for t in deglue(text).split("\n")]
    return "\n".join(line.text for line in clean_lines(lines))
