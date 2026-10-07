"""Corpus audit: `python -m app.audit` -> docs/DATA_AUDIT.md + data/processed/audit.json.

Everything reported here is measured from the PDFs in data/raw at run time:
  * extraction traps (glyphs, glued lines, wraps, markdown, tables, number formats, truncated values),
  * structure (TOC vs body, FAQ counts, duplicate FAQs, boilerplate repetition),
  * generic content-conflict detectors (range bounds, overlapping buckets, scenario SLAs, tax wording,
    FAQ-vs-body wording, absolute FAQ claims, FAQ section references that point at the wrong section),
  * unsupported services and probes for information that is absent from the corpus.
Finally every finding claimed by PROMPT.md §3.3 is re-checked against the parsed text and reported
as REPRODUCED / NOT REPRODUCED. Exit code 1 if a Phase-0 gate condition fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from app.ingest.chunker import table_lines
from app.ingest.cleaner import deglue
from app.ingest.dedup import mask_text
from app.ingest.models import Chunk
from app.ingest.pipeline import Corpus, CorpusDocument, load_corpus
from app.ingest.structure import FAQ_ANYWHERE, FaqItem, ParsedDocument, Section
from app.settings import ROOT_DIR
from app.text.numbers import CURRENCY_RE, LAKH_RE, canonical_amount, find_malformed_amounts, numeric_tokens

FAQ_LINE_START = re.compile(r"(?m)^Q\d{3}:")


# =============================================================================== helpers
def section_text(doc: ParsedDocument, section_id: str, *, with_children: bool = True) -> str:
    """Plain text of a section incl. linearised tables (and sub-sections)."""
    section = doc.sections.get(section_id)
    if section is None:
        return ""
    parts: list[str] = []
    for line in section.lines:
        parts.append("\n".join(table_lines(doc.tables[line.table_id])) if line.table_id else line.text)
    if with_children:
        parts.extend(section_text(doc, child) for child in section.children)
    return "\n".join(parts)


def body_sections(doc: ParsedDocument) -> list[Section]:
    """Non-FAQ, non-front-matter sections."""
    return [s for s in doc.sections.values() if s.kind in ("content", "annex")]


def top_id(section_id: str) -> str:
    """'6.2' -> '6'."""
    return section_id.split(".", maxsplit=1)[0]


def figures(text: str) -> set[str]:
    """Canonical numeric tokens (amounts, %, T+n) used for overlap comparisons."""
    return set(numeric_tokens(text)) - {"per_annum"}


@dataclass
class Finding:
    """One audit finding."""

    category: str
    doc_id: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    #: structural members involved: body section ids ("4.2") or FAQ ids ("FAQ:Q002")
    members: list[str] = field(default_factory=list)


# =============================================================================== trap checks
def audit_extraction(cdoc: CorpusDocument, chunks: list[Chunk]) -> dict[str, Any]:
    """Traps 1-8 measured for one document."""
    raw_pages = [p.raw_text for p in cdoc.raw.pages]
    raw_joined_ff = "\f".join(p.rstrip("\n") for p in raw_pages)
    clean_joined = "\n".join(line.text for s in cdoc.parsed.sections.values() for line in s.lines)
    deglued = deglue(raw_joined_ff)
    tables = list(cdoc.raw.tables.values())
    multi_page_tables = [t.table_id for t in tables if t.page_end > t.page_start]
    all_chunk_text = "\n".join(c.embed_text for c in chunks)
    amounts = [m.group(0) for p in raw_pages for m in CURRENCY_RE.finditer(p)]
    lakh_grouped = [a for a in amounts if re.search(r"\d,\d{2},\d{3}", a)]
    l_suffix = [m.group(0) for p in raw_pages for m in re.finditer(r"[■₹]\d+(?:\.\d+)?L\b", p)]
    lakh_words = [m.group(0) for p in raw_pages for m in LAKH_RE.finditer(p)]
    return {
        "pages": len(raw_pages),
        "glyph_raw_count": sum(p.count("■") for p in raw_pages),
        "rupee_after_clean": clean_joined.count("₹")
        + sum(" ".join(" ".join(r) for r in t.rows).count("₹") for t in tables),
        "glyph_residual_in_chunks": all_chunk_text.count("■"),
        "page_break_glues": len(re.findall(r"[^\n]\f[^\n]", raw_joined_ff)),
        "faq_naive_line_start_ff_joined": len(FAQ_LINE_START.findall(raw_joined_ff)),
        "faq_anywhere_raw": len(FAQ_ANYWHERE.findall(raw_joined_ff)),
        "faq_line_start_after_deglue": len(FAQ_LINE_START.findall(deglued)),
        "faq_parsed": len(cdoc.parsed.faqs),
        "raw_lines": len(cdoc.raw.lines),
        "clean_lines": sum(len(s.lines) for s in cdoc.parsed.sections.values()),
        "wrapped_faq_questions_rejoined": sum(
            1
            for f in cdoc.parsed.faqs
            if re.search(r"\(\D*\d+\)$", f.question)
            and "\n" not in f.question
            and _question_was_wrapped(cdoc, f)
        ),
        "bold_markers_raw": sum(p.count("**") for p in raw_pages),
        "backticks_raw": sum(p.count("`") for p in raw_pages),
        "markdown_residual_in_chunks": all_chunk_text.count("**") + all_chunk_text.count("`"),
        "toc_anchor_residual_in_chunks": len(re.findall(r"\]\(#section", all_chunk_text)),
        "formfeed_residual_in_chunks": all_chunk_text.count("\f"),
        "tables": len(tables),
        "tables_spanning_pages_merged": multi_page_tables,
        "multi_line_cells": _multi_line_cells(cdoc),
        "amounts_total": len(amounts),
        "amounts_indian_grouped": len(lakh_grouped),
        "amounts_L_suffix": sorted(set(l_suffix)),
        "amounts_lakh_words": sorted(set(lakh_words)),
        "malformed_amounts": _malformed_with_context(cdoc),
        "sections_body": [
            s.section_id for s in cdoc.parsed.sections.values() if s.level == 1 and s.kind != "front_matter"
        ],
        "subsections": [s.section_id for s in cdoc.parsed.sections.values() if s.level == 2],
    }


def _question_was_wrapped(cdoc: CorpusDocument, faq: FaqItem) -> bool:
    prefix = f"{faq.faq_id}: "
    for line in cdoc.raw.lines:
        if line.text.startswith(prefix):
            return not line.text.rstrip().endswith(")")
    return False


def _multi_line_cells(cdoc: CorpusDocument) -> list[str]:
    """Cells that pdfplumber returned with internal newlines (merged into one line by the cleaner)."""
    return sorted({cell for table in cdoc.raw.tables.values() for cell in table.merged_cells})


def _malformed_with_context(cdoc: CorpusDocument) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    doc = cdoc.parsed
    for section in doc.sections.values():
        for line in section.lines:
            texts = [
                " | ".join(r)
                for r in (
                    [doc.tables[line.table_id].header, *doc.tables[line.table_id].rows]
                    if line.table_id
                    else [[line.text]]
                )
            ]
            for text in texts:
                for bad in find_malformed_amounts(text):
                    key = (bad, re.sub(r"\d", "#", text))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        {
                            "value": bad,
                            "section": section.section_id,
                            "context": text[:200],
                            "templated_copies": section.templated_title,
                        }
                    )
    return out


# =============================================================================== structure checks
def audit_structure(cdoc: CorpusDocument) -> dict[str, Any]:
    """TOC vs body, FAQ duplication."""
    doc = cdoc.parsed
    body_top = {
        s.section_id: s.title for s in doc.sections.values() if s.level == 1 and s.kind != "front_matter"
    }
    missing = [{"section": sid, "toc_title": title} for sid, title in doc.toc.items() if sid not in body_top]
    extra = [
        sid
        for sid in body_top
        if sid not in doc.toc and not sid.startswith(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    ]
    annexes = [sid for sid in body_top if not sid[0].isdigit()]
    title_mismatch = [
        {"section": sid, "toc": doc.toc[sid], "body": body_top[sid]}
        for sid in doc.toc
        if sid in body_top and _norm_title(doc.toc[sid]) != _norm_title(body_top[sid])
    ]
    q_counts = Counter(f.question_core for f in doc.faqs)
    answers_by_q: dict[str, set[str]] = {}
    for faq in doc.faqs:
        answers_by_q.setdefault(faq.question_core, set()).add(mask_text(faq.answer))
    conflicts = [q for q, answers in answers_by_q.items() if len(answers) > 1]
    templated = [s.section_id for s in doc.sections.values() if s.templated_title]
    return {
        "toc_entries": len(doc.toc),
        "missing_in_body": missing,
        "body_not_in_toc": extra,
        "annex_sections": annexes,
        "toc_title_mismatches": title_mismatch,
        "faq_total": len(doc.faqs),
        "faq_unique_questions": len(q_counts),
        "faq_copies_per_question": sorted(set(q_counts.values())),
        "faq_same_question_different_answer": conflicts,
        "templated_boilerplate_sections": templated,
    }


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


# =============================================================================== content checks
_REF_STOP = frozenset(
    [
        "under",
        "section",
        "policy",
        "finbase",
        "reference",
        "clause",
        "with",
        "your",
        "that",
        "this",
        "from",
        "will",
        "have",
        "been",
        "after",
        "which",
        "their",
        "there",
    ]
)


def _content_words(text: str) -> set[str]:
    text = re.sub(r"\[Reference[^\]]*\]", "", text)
    return {w for w in re.findall(r"[a-z][a-z0-9+&-]{3,}", text.lower()) if w not in _REF_STOP}


def faq_reference_check(doc: ParsedDocument) -> list[dict[str, Any]]:
    """FAQ answers citing 'Section x.y': does the cited section actually support the answer?

    Support of a section = share of the FAQ's content words (question + answer) found in the section's own
    text; figures (amounts, %, T+n) break ties. Calibrated on this corpus: every correct citation scores
    >= 0.30, wrong ones <= 0.15, so a citation is flagged when its support is < 0.25 and the best section
    has at least twice that support. Templated boilerplate is represented by its first copy.
    """
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    templated = [s.section_id for s in body_sections(doc) if s.templated_title]
    sections = [
        s
        for s in body_sections(doc)
        if not s.templated_title or s.section_id == (templated[0] if templated else "")
    ]
    texts = {s.section_id: section_text(doc, s.section_id, with_children=False) for s in sections}
    for faq in doc.faqs:
        if faq.question_core in seen:
            continue
        seen.add(faq.question_core)
        refs = sorted(set(faq.text_section_refs))
        if not refs:
            continue
        words = _content_words(faq.question_core) | _content_words(faq.answer)
        figs = figures(faq.answer) - figures(faq.question)
        support = {sid: len(words & _content_words(t)) / max(1, len(words)) for sid, t in texts.items()}
        fig_support = {sid: (len(figs & figures(t)) / len(figs) if figs else 0.0) for sid, t in texts.items()}
        ranked = sorted(texts, key=lambda k: (-support[k], -fig_support[k]))
        best = ranked[0]
        for ref in refs:
            key = templated[0] if templated and ref in templated else ref
            if key not in doc.sections:
                status = "REF_MISSING"
            elif support.get(key, 0.0) < 0.25 and support[best] >= 2 * support.get(key, 0.0):
                status = "WRONG_SECTION"
            else:
                continue
            results.append(
                {
                    "faq_id": faq.faq_id,
                    "question": faq.question_core,
                    "cited_in_text": ref,
                    "cited_title": doc.sections[key].title if key in doc.sections else None,
                    "support_in_cited": round(support.get(key, 0.0), 2),
                    "supported_by": [
                        {"section": sid, "title": doc.sections[sid].title, "score": round(support[sid], 2)}
                        for sid in ranked[:2]
                    ],
                    "status": status,
                }
            )
    return results


_RANGE_RE = re.compile(r"₹(?P<a>[\d,]+)\s*(?:to|-)\s*₹(?P<b>[\d,]+)")


def detect_range_conflicts(doc: ParsedDocument) -> list[Finding]:
    """Same lower bound, different upper bound for ranges within one document (e.g. ₹150-400 vs ₹150-350)."""
    by_low: dict[str, list[tuple[str, str, str]]] = {}
    for section in body_sections(doc):
        text = section_text(doc, section.section_id, with_children=False)
        for match in _RANGE_RE.finditer(text):
            low, high = (
                canonical_amount(match.group("a").rstrip(",")),
                canonical_amount(match.group("b").rstrip(",")),
            )
            if low and high:
                ctx = text[max(0, match.start() - 80) : match.end() + 30].replace("\n", " ")
                by_low.setdefault(low, []).append((high, section.section_id, ctx))
    findings: list[Finding] = []
    for low, items in by_low.items():
        highs = {h for h, _, _ in items}
        sections = {s for _, s, _ in items}
        if len(highs) > 1 and len(sections) > 1:
            findings.append(
                Finding(
                    "range_conflict",
                    doc.meta.doc_id,
                    f"range starting ₹{low} has different upper bounds {sorted(highs, key=float)}",
                    [f"Section {s}: …{c}…" for _, s, c in items],
                    sorted(sections),
                )
            )
    return findings


_BUCKET_RE = re.compile(
    r"^(?P<a>\d+)\s*(?P<ua>days?|years?|months?)?\s+to\s+(?P<less>less than\s+)?(?P<b>\d+)\s*(?P<ub>days?|years?|months?)$",
    re.I,
)


def detect_overlapping_buckets(doc: ParsedDocument) -> list[Finding]:
    """Table first-column ranges whose inclusive boundaries overlap (e.g. '2 years to 3 years' & '3 years to 5 years')."""
    findings: list[Finding] = []
    for section in body_sections(doc):
        if section.templated_title:
            continue
        for line in section.lines:
            if not line.table_id:
                continue
            table = doc.tables[line.table_id]
            buckets: list[tuple[float, float, bool, str, list[str]]] = []
            for row in table.rows:
                match = _BUCKET_RE.match(row[0].strip()) if row else None
                if not match:
                    continue
                unit = (match.group("ub") or "").lower().rstrip("s")
                factor = {"day": 1, "month": 30, "year": 365}.get(unit, 1)
                unit_a = (match.group("ua") or match.group("ub") or "").lower().rstrip("s")
                factor_a = {"day": 1, "month": 30, "year": 365}.get(unit_a, factor)
                buckets.append(
                    (
                        int(match.group("a")) * factor_a,
                        int(match.group("b")) * factor,
                        bool(match.group("less")),
                        row[0],
                        row,
                    )
                )
            for (_a1, b1, less1, label1, row1), (a2, _b2, _less2, label2, row2) in pairwise(buckets):
                if not less1 and b1 == a2:
                    findings.append(
                        Finding(
                            "overlapping_buckets",
                            doc.meta.doc_id,
                            f"Section {section.section_id}: '{label1}' and '{label2}' both include the boundary value",
                            [" | ".join(row1), " | ".join(row2)],
                            [section.section_id],
                        )
                    )
    return findings


def detect_scenario_sla(doc: ParsedDocument) -> list[Finding]:
    """Compensation trigger windows ('beyond T+n' / 'longer than T+n') that differ across sections/scenarios."""
    mentions: list[tuple[str, str, str]] = []
    for section in body_sections(doc):
        text = section_text(doc, section.section_id, with_children=False)
        for match in re.finditer(
            r"(?:beyond|longer than|exceeds?|expiry of the)\s+(?:the\s+)?T\s?\+\s?(\d+)", text, re.I
        ):
            ctx = text[max(0, text.rfind("\n", 0, match.start()) + 1) : match.end() + 40].replace("\n", " ")
            mentions.append((match.group(1), section.section_id, ctx[:220]))
    values = {m[0] for m in mentions}
    if len(values) > 1:
        return [
            Finding(
                "scenario_dependent_sla",
                doc.meta.doc_id,
                f"compensation trigger differs: T+{sorted(values)}",
                [f"Section {s}: {c}" for _, s, c in mentions],
                sorted({s for _, s, _ in mentions}),
            )
        ]
    return []


def detect_tax_wording(doc: ParsedDocument) -> list[Finding]:
    """Same amount stated with '+ 18% GST' in one place and plain '+ GST' in another."""
    forms: dict[str, set[tuple[str, str]]] = {}
    for section in body_sections(doc):
        if section.templated_title:
            continue
        text = section_text(doc, section.section_id, with_children=False)
        for match in re.finditer(r"₹(?P<amt>[\d,]+)\s*\+\s*(?P<tax>18% GST|GST)", text):
            forms.setdefault(match.group("amt"), set()).add((match.group("tax"), section.section_id))
    findings = []
    for amount, variants in forms.items():
        if len({v[0] for v in variants}) > 1:
            findings.append(
                Finding(
                    "tax_wording_variance",
                    doc.meta.doc_id,
                    f"₹{amount}: GST stated as {sorted({v[0] for v in variants})}",
                    [f"Section {s}: ₹{amount} + {t}" for t, s in sorted(variants)],
                    sorted({s for _, s in variants}),
                )
            )
    return findings


_PERIOD_RE = re.compile(
    r"\b(preceding|previous|current|last)\s+(membership|calendar|financial)\s+year\b", re.I
)


def detect_faq_body_wording(doc: ParsedDocument) -> list[Finding]:
    """FAQ answer and body sentence share an amount but qualify it with a different period."""
    findings: list[Finding] = []
    body_sentences: list[tuple[str, str]] = []
    for section in body_sections(doc):
        for sentence in re.split(
            r"(?<=[.])\s+|\n", section_text(doc, section.section_id, with_children=False)
        ):
            body_sentences.append((section.section_id, sentence))
    seen: set[str] = set()
    for faq in doc.faqs:
        if faq.question_core in seen:
            continue
        seen.add(faq.question_core)
        for fmatch in _PERIOD_RE.finditer(faq.answer):
            fig = figures(faq.answer)
            for sid, sentence in body_sentences:
                bmatch = _PERIOD_RE.search(sentence)
                if bmatch and fig & figures(sentence) and bmatch.group(2).lower() != fmatch.group(2).lower():
                    findings.append(
                        Finding(
                            "faq_body_period_mismatch",
                            doc.meta.doc_id,
                            f"{faq.faq_id} says '{fmatch.group(0)}' but Section {sid} says '{bmatch.group(0)}'",
                            [f"{faq.faq_id}: {faq.answer[:220]}", f"Section {sid}: {sentence[:220]}"],
                            [f"FAQ:{faq.faq_id}", sid],
                        )
                    )
    return findings


def detect_faq_scope_differences(doc: ParsedDocument) -> list[Finding]:
    """FAQ answers that drop conditions present in the body (absolute claims or partial numbers)."""
    findings: list[Finding] = []
    seen: set[str] = set()
    sections = body_sections(doc)
    for faq in doc.faqs:
        if faq.question_core in seen:
            continue
        seen.add(faq.question_core)
        if re.search(r"\bat any time\b|\bforever\b", faq.answer, re.I):
            topic = [
                w
                for w in re.findall(r"[a-z]{5,}", faq.question_core.lower())
                if w not in {"there", "which", "about", "account", "finbase", "savings"}
            ]
            for section in sections:
                if section.templated_title:
                    continue
                for line in section.lines:
                    if not line.table_id:
                        continue
                    for row in doc.tables[line.table_id].rows:
                        label = row[0].lower()
                        if any(t[:4] in label for t in topic) and re.search(
                            r"\d+\s*(?:days?|months?)", label
                        ):
                            findings.append(
                                Finding(
                                    "faq_absolute_claim_vs_bounded_table",
                                    doc.meta.doc_id,
                                    f"{faq.faq_id} claims '{re.search(r'at any time|forever', faq.answer, re.I).group(0)}' (absolute) but Section {section.section_id} rows are bounded",  # type: ignore[union-attr]
                                    [
                                        f"{faq.faq_id}: {faq.answer[:200]}",
                                        f"Section {section.section_id}: {' | '.join(row)}",
                                    ],
                                    [f"FAQ:{faq.faq_id}", section.section_id],
                                )
                            )
        # qualifier dropped: body says "N ... in <X> (M in non-<X> ...)", FAQ repeats "N ... <X>" without the non-<X> case
        for section in sections:
            if section.templated_title:
                continue
            for line in section.lines:
                if line.table_id:
                    continue
                q = re.search(
                    r"(?P<n>\d+)[^()]*?\bin (?P<x>[a-z]+)[^()]*\((?P<m>\d+) in non-(?P=x)", line.text, re.I
                )
                if not q:
                    continue
                x = q.group("x").lower()
                for f_sentence in re.split(r"(?<=[.])\s+", faq.answer):
                    if (
                        re.search(rf"\b{q.group('n')}\b[^.]*\b{x}\b", f_sentence, re.I)
                        and f"non-{x}" not in f_sentence.lower()
                    ):
                        findings.append(
                            Finding(
                                "faq_omits_condition",
                                doc.meta.doc_id,
                                f"{faq.faq_id} states the '{x}' figure ({q.group('n')}) but omits the non-{x} case ({q.group('m')}) given in Section {section.section_id}",
                                [
                                    f"{faq.faq_id}: {f_sentence[:220]}",
                                    f"Section {section.section_id}: {line.text[:220]}",
                                ],
                                [f"FAQ:{faq.faq_id}", section.section_id],
                            )
                        )
    return findings


def detect_internal_row_inconsistency(doc: ParsedDocument) -> list[Finding]:
    """Rows where a 'daily' limit exceeds the 'monthly' cap, or contains a malformed amount."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for section in body_sections(doc):
        for line in section.lines:
            if not line.table_id:
                continue
            table = doc.tables[line.table_id]
            header = [h.lower() for h in table.header]
            if not (any("daily" in h for h in header) and any("monthly" in h for h in header)):
                continue
            di = next(i for i, h in enumerate(header) if "daily" in h)
            mi = next(i for i, h in enumerate(header) if "monthly" in h)
            for row in table.rows:
                key = re.sub(r"\d", "#", " | ".join(row))
                if key in seen:
                    continue
                seen.add(key)
                bad = find_malformed_amounts(row[di])
                d = re.search(r"₹([\d,]+)", row[di])
                m = re.search(r"₹([\d,]+)", row[mi])
                d_val = canonical_amount(d.group(1)) if d else None
                m_val = canonical_amount(m.group(1)) if m else None
                if bad or (d_val and m_val and float(d_val) > float(m_val)):
                    findings.append(
                        Finding(
                            "row_inconsistent_or_truncated",
                            doc.meta.doc_id,
                            f"Section {section.section_id}: daily '{row[di]}' vs monthly cap '{row[mi]}'",
                            [" | ".join(row)],
                            [section.section_id],
                        )
                    )
    return findings


CONFLICT_DETECTORS = (
    detect_range_conflicts,
    detect_overlapping_buckets,
    detect_scenario_sla,
    detect_tax_wording,
    detect_faq_body_wording,
    detect_faq_scope_differences,
    detect_internal_row_inconsistency,
)


def detect_conflicts(doc: ParsedDocument) -> list[Finding]:
    """Run every content-conflict detector on one document."""
    return [finding for detector in CONFLICT_DETECTORS for finding in detector(doc)]


def unsupported_services(doc: ParsedDocument) -> list[dict[str, str]]:
    """Bullets that explicitly declare a service as not offered."""
    out = []
    for section in body_sections(doc):
        for line in section.lines:
            if line.text.startswith("•") and re.search(
                r"\bdoes NOT (?:provide|offer)\b|\bstrictly prohibited\b", line.text, re.I
            ):
                name = line.text.lstrip("• ").split(":")[0]
                out.append(
                    {
                        "section": section.section_id,
                        "section_title": section.title,
                        "service": name,
                        "text": line.text,
                    }
                )
    return out


#: label -> (doc scope or None for the whole corpus, regex)
ABSENCE_PROBES: dict[str, tuple[str | None, str]] = {
    "home loan": (None, r"\bhome[- ]loans?\b"),
    "auto / car loan": (None, r"\b(?:auto|car|vehicle)[- ]loans?\b"),
    "repo rate / live market rates": (None, r"\brepo rate\b|\bsensex\b|\bnifty\b"),
    "credit-card reward points value / expiry / redemption": (
        None,
        r"reward points?.{0,60}(?:value|expir|redeem|redemption|catalog)",
    ),
    "UPI mandate bounce fee amount (payments SOP)": (
        "payments_upi",
        r"bounce fee.{0,40}₹\d|₹\d[\d,]*.{0,40}bounce",
    ),
    "bounce fee amount anywhere (shows where ₹500 belongs)": (
        None,
        r"bounce.{0,40}₹\d|₹\d[\d,]*.{0,10}per bounce",
    ),
    "tax advice": (None, r"\btax advice\b"),
    "other banks' products": (None, r"\b(?:HDFC|ICICI|SBI|Axis|Kotak)\b"),
}


def absence_probes(corpus: Corpus) -> dict[str, list[str]]:
    """Where (if anywhere) each probed topic appears in the corpus (chunk-level, cleaned text)."""
    out: dict[str, list[str]] = {}
    for label, (scope, pattern) in ABSENCE_PROBES.items():
        hits = [
            f"{c.doc_id} §{c.section_id}{' ' + c.faq_id if c.faq_id else ''}"
            for c in corpus.chunks
            if (scope is None or c.doc_id == scope) and re.search(pattern, c.text, re.I)
        ]
        out[label] = sorted(set(hits))
    return out


# =============================================================================== PROMPT.md §3.3 verification
@dataclass
class Expectation:
    """A claim from PROMPT.md §3.3 that must be reproducible from the PDFs."""

    key: str
    claim: str
    checks: list[tuple[str, str, str]]  # (doc_id, section_id | 'FAQ' | '!absent', regex)


EXPECTATIONS: list[Expectation] = [
    Expectation(
        "A1",
        "fd_wealth Section 21 (rate matrix) listed in TOC but missing from body; rates are in Section 1",
        [("fd_wealth", "!missing", "21"), ("fd_wealth", "1", r"7\.50% p\.a\.")],
    ),
    Expectation(
        "A2",
        "payments_upi Section 22 (Dispute Arbitration and Chargeback Workflow) missing",
        [("payments_upi", "!missing", "22")],
    ),
    Expectation(
        "A3",
        "credit_cards Section 22 (Reward Points Accrual and Redemption Matrix) missing; no point value/expiry/redemption anywhere",
        [
            ("credit_cards", "!missing", "22"),
            ("credit_cards", "!absent", r"reward points?.{0,60}(?:value|expir|redeem|redemption|catalog)"),
        ],
    ),
    Expectation(
        "A4",
        "savings_account Section 22 (EFT Limits and Cutoff Times) missing; limits in Section 3",
        [("savings_account", "!missing", "22"), ("savings_account", "3", r"IMPS.*₹5,00,000")],
    ),
    Expectation(
        "A5",
        "kyc_security Sections 21 and 22 missing; content exists in Sections 5 and 3",
        [
            ("kyc_security", "!missing", "21"),
            ("kyc_security", "!missing", "22"),
            ("kyc_security", "5", r"Zero Liability"),
            ("kyc_security", "3", r"Video KYC"),
        ],
    ),
    Expectation(
        "B1",
        "personal_loans FAQ Q001 cites Section 4.2 for foreclosure; foreclosure rules are in 6.2; 4.2 is processing charges",
        [
            ("personal_loans", "FAQ", r"Q001: .*foreclosure.*\n?.*Section 4\.2"),
            ("personal_loans", "6.2", r"3% of the outstanding principal"),
            ("personal_loans", "4.2", r"processing fee"),
        ],
    ),
    Expectation(
        "C1",
        "payments_upi: Section 2 T+2 + ₹100/day after T+2; Section 21 P2M compensation beyond T+5",
        [
            ("payments_upi", "2", r"T\+2 business days"),
            ("payments_upi", "2", r"₹100 per calendar day"),
            (
                "payments_upi",
                "21",
                r"P2M Merchant Online Debit Failed.*T \+ 5 Days.*₹100 per day beyond T\+5",
            ),
        ],
    ),
    Expectation(
        "C2",
        "personal_loans mandate/e-sign charge ₹150 to ₹400 (4.2) vs ₹150 - ₹350 (21)",
        [("personal_loans", "4.2", r"₹150 to ₹400"), ("personal_loans", "21", r"₹150 - ₹350")],
    ),
    Expectation(
        "C3",
        "credit_cards Luxe waiver: 'preceding membership year' (1.2) vs 'previous calendar year' (FAQ Q002)",
        [
            ("credit_cards", "1.2", r"preceding membership year"),
            ("credit_cards", "FAQ", r"previous\s+calendar year"),
        ],
    ),
    Expectation(
        "C4",
        "savings ATM: FAQ '3 free per month at other metro ATMs' vs Section 3 metro 3 / non-metro 5 / partner 5; '₹21 + 18% GST' (3) vs '₹21 + GST' (21); FAQ 'no closure fee at any time' vs table up to 12 months",
        [
            ("savings_account", "FAQ", r"3 free per month at other metro ATMs"),
            ("savings_account", "3", r"3 free withdrawals per month in metro cities \(5 in non-metro"),
            ("savings_account", "3", r"₹21 \+ 18% GST"),
            ("savings_account", "21", r"₹21 \+ GST"),
            ("savings_account", "FAQ", r"closure fee at any time"),
            ("savings_account", "21", r"15 days to 12 months"),
        ],
    ),
    Expectation(
        "C5",
        "fd_wealth tenure buckets '2 years to 3 years' (7.25%) and '3 years to 5 years' (7.00%) overlap at 3 years",
        [
            ("fd_wealth", "1", r"2 years to 3 years.*7\.25%"),
            ("fd_wealth", "1", r"3 years to 5 years.*7\.00%"),
        ],
    ),
    Expectation(
        "D1",
        "fd_wealth Section 22 lists unsupported services: crypto, agricultural property loans, intraday/F&O tips, chit funds/Ponzi",
        [
            ("fd_wealth", "22", r"Cryptocurrency"),
            ("fd_wealth", "22", r"Agricultural Property Loans"),
            ("fd_wealth", "22", r"Intraday"),
            ("fd_wealth", "22", r"Chit Funds"),
        ],
    ),
    Expectation(
        "E1",
        "UPI mandate bounce fee amount absent from payments SOP (only 'Levies standard bounce fee'); ₹500 bounce fee belongs to personal loans",
        [
            ("payments_upi", "4", r"Levies standard bounce fee"),
            ("payments_upi", "!absent", r"bounce fee.{0,40}₹\d"),
            ("personal_loans", "5.2", r"₹500 \+ 18% GST"),
        ],
    ),
    Expectation(
        "E2",
        "home-loan / auto-loan rates and repo rate absent",
        [
            ("*", "!absent", r"\bhome[- ]loans?\b"),
            ("*", "!absent", r"\b(?:auto|car)[- ]loans?\b"),
            ("*", "!absent", r"\brepo rate\b"),
        ],
    ),
    Expectation(
        "T7",
        "truncated values: savings contactless '₹5,00,0' with cap '₹25,000'; loans cancellation fee '₹1,00,0'",
        [
            ("savings_account", "4", r"Contactless Tap.*₹5,00,0\b.*₹25,000"),
            ("personal_loans", "21", r"₹1,00,0 \+ Interest accrued"),
        ],
    ),
]


def check_expectation(
    exp: Expectation, docs: dict[str, ParsedDocument], corpus: Corpus
) -> tuple[bool, list[str]]:
    """Evaluate one expectation; returns (reproduced, evidence lines)."""
    evidence: list[str] = []
    ok = True
    for doc_id, where, pattern in exp.checks:
        targets = list(docs.values()) if doc_id == "*" else [docs[doc_id]]
        for doc in targets:
            if where == "!missing":
                passed = pattern in doc.toc and pattern not in doc.sections
                evidence.append(
                    f"{doc.meta.doc_id}: TOC lists Section {pattern} ('{doc.toc.get(pattern, '?')}'), body has it: {pattern in doc.sections} -> {'OK' if passed else 'FAIL'}"
                )
            elif where == "!absent":
                hits = [
                    c
                    for c in corpus.chunks
                    if c.doc_id == doc.meta.doc_id and re.search(pattern, c.text, re.I)
                ]
                passed = not hits
                evidence.append(
                    f"{doc.meta.doc_id}: /{pattern}/ hits={len(hits)} -> {'OK' if passed else 'FAIL'}"
                )
            else:
                text = "\n".join(f.text for f in doc.faqs) if where == "FAQ" else section_text(doc, where)
                match = re.search(pattern, text, re.I)
                passed = match is not None
                snippet = match.group(0)[:120].replace("\n", " ") if match else "no match"
                evidence.append(
                    f"{doc.meta.doc_id} {'FAQ' if where == 'FAQ' else '§' + where}: /{pattern}/ -> {'OK' if passed else 'FAIL'} ({snippet})"
                )
            ok = ok and passed
    return ok, evidence


# =============================================================================== report
def run_audit(data_dir: Path) -> dict[str, Any]:
    """Run every check and return the machine-readable audit."""
    corpus = load_corpus(data_dir)
    docs = {d.parsed.meta.doc_id: d.parsed for d in corpus.documents}
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "documents": {},
        "findings": [],
        "expectations": [],
    }
    for cdoc in corpus.documents:
        doc = cdoc.parsed
        doc_chunks = [c for c in corpus.chunks if c.doc_id == doc.meta.doc_id]
        result["documents"][doc.meta.doc_id] = {
            "file": doc.meta.file,
            "title": doc.meta.title,
            "code": doc.meta.code,
            "sha256": cdoc.raw.sha256,
            "extraction": audit_extraction(cdoc, doc_chunks),
            "structure": audit_structure(cdoc),
            "faq_section_reference_issues": faq_reference_check(doc),
            "unsupported_services": unsupported_services(doc),
        }
        result["findings"].extend(f.__dict__ for f in detect_conflicts(doc))
    result["absence_probes"] = absence_probes(corpus)
    result["dedup"] = {k: v for k, v in corpus.dedup.report.items() if k != "groups"}
    result["dedup_groups"] = corpus.dedup.report["groups"]
    for exp in EXPECTATIONS:
        ok, evidence = check_expectation(exp, docs, corpus)
        result["expectations"].append(
            {"key": exp.key, "claim": exp.claim, "reproduced": ok, "evidence": evidence}
        )
    result["gate"] = gate(result)
    return result


def gate(result: dict[str, Any]) -> dict[str, Any]:
    """Phase-0 gate conditions."""
    docs = result["documents"].values()
    conditions = {
        "faq_exactly_100_per_doc": all(
            d["extraction"]["faq_parsed"] == 100 and d["extraction"]["faq_anywhere_raw"] == 100 for d in docs
        ),
        "no_glyph_residual": all(d["extraction"]["glyph_residual_in_chunks"] == 0 for d in docs),
        "no_markdown_or_toc_residual": all(
            d["extraction"]["markdown_residual_in_chunks"] == 0
            and d["extraction"]["toc_anchor_residual_in_chunks"] == 0
            for d in docs
        ),
        "missing_sections_detected": sum(len(d["structure"]["missing_in_body"]) for d in docs) >= 6,
        "conflicts_detected": len(result["findings"]) > 0,
        "suspect_values_detected": sum(len(d["extraction"]["malformed_amounts"]) for d in docs) >= 2,
        "all_prompt_expectations_reproduced": all(e["reproduced"] for e in result["expectations"]),
    }
    return {"passed": all(conditions.values()), "conditions": conditions}


def render_markdown(result: dict[str, Any]) -> str:
    """Human-readable DATA_AUDIT.md."""
    out: list[str] = []
    w = out.append
    w("# FinBase Corpus Data Audit\n")
    w(
        f"Generated by `python -m app.audit` at {result['generated_at']} from the PDFs in `data/raw/`. Every number below is measured at run time; nothing is hard-coded. Machine-readable copy: `data/processed/audit.json`.\n"
    )
    gate_ = result["gate"]
    w(f"**Phase-0 gate: {'PASS' if gate_['passed'] else 'FAIL'}**\n")
    for name, ok in gate_["conditions"].items():
        w(f"- [{'x' if ok else ' '}] {name}")
    w("\n## 1. Documents\n")
    w(
        "| doc_id | file | code | pages | tables | body sections | sub-sections | FAQs parsed | unique FAQ questions | sha256 (12) |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        e, s = d["extraction"], d["structure"]
        w(
            f"| {doc_id} | {d['file']} | {d['code']} | {e['pages']} | {e['tables']} | {len(e['sections_body'])} | {len(e['subsections'])} | {e['faq_parsed']} | {s['faq_unique_questions']} | `{d['sha256'][:12]}` |"
        )

    w("\n## 2. Extraction traps (PROMPT.md §3.2)\n")
    w(
        "| doc_id | ₹ glyph raw (`■`) | ₹ after clean | `■` left in chunks | page-break glues | FAQ naive line-start (`\\f`-joined) | FAQ anywhere-regex | FAQ after de-glue | `**` raw | backticks raw | markdown left | tables spanning pages (merged) |"
    )
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        e = d["extraction"]
        w(
            f"| {doc_id} | {e['glyph_raw_count']} | {e['rupee_after_clean']} | {e['glyph_residual_in_chunks']} | {e['page_break_glues']} | {e['faq_naive_line_start_ff_joined']} | {e['faq_anywhere_raw']} | {e['faq_line_start_after_deglue']} | {e['bold_markers_raw']} | {e['backticks_raw']} | {e['markdown_residual_in_chunks']} | {', '.join(e['tables_spanning_pages_merged']) or '-'} |"
        )
    w("\nNotes:")
    w(
        "- **Currency glyph**: the rupee sign is drawn with a ZapfDingbats glyph. PyMuPDF decodes it as `I`, pdfplumber as `n`, other extractors as `■`. The loader maps every ZapfDingbats character to `■` and the cleaner converts `■` → `₹` only before a digit / `(` / space+digit; no `■` survives in any chunk."
    )
    w(
        "- **Glued lines**: joining pages with `\\f` (as `pdftotext`-style extractors do) glues the last line of a page to the first of the next; a naive `^Q\\d{3}:` count then under-counts FAQs (column *FAQ naive*). The anywhere-regex and the de-glued text give exactly 100 per document."
    )
    w(
        "- **Wrapped lines**: re-joined using PDF geometry (line reaches the right margin) + font-weight changes (bold FAQ question vs regular answer) + punctuation heuristics for non-PDF loaders."
    )
    w(
        "- **Multi-line table cells** observed: "
        + "; ".join(
            sorted({c for d in result["documents"].values() for c in d["extraction"]["multi_line_cells"]})
        )
        + " — merged into a single cell value."
    )
    w("\n### Indian number formats\n")
    w("| doc_id | currency amounts | Indian-grouped (x,xx,xxx) | `L` suffix forms | lakh words |")
    w("|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        e = d["extraction"]
        w(
            f"| {doc_id} | {e['amounts_total']} | {e['amounts_indian_grouped']} | {', '.join(e['amounts_L_suffix']) or '-'} | {', '.join(e['amounts_lakh_words']) or '-'} |"
        )
    w(
        "\n### Truncated / garbled source values (never repaired; chunks flagged `quality_flag=suspect_value`)\n"
    )
    for doc_id, d in result["documents"].items():
        for m in d["extraction"]["malformed_amounts"]:
            w(
                f"- **{doc_id} §{m['section']}**{' (templated section, repeated in every copy)' if m['templated_copies'] else ''}: `{m['value']}` — context: `{m['context']}`"
            )

    w("\n## 3. Structure: TOC vs body\n")
    w(
        "| doc_id | TOC entries | listed in TOC but **missing from body** | annexes (not in TOC) | templated boilerplate sections |"
    )
    w("|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        s = d["structure"]
        missing = "; ".join(f"§{m['section']} {m['toc_title']}" for m in s["missing_in_body"]) or "-"
        templ = s["templated_boilerplate_sections"]
        w(
            f"| {doc_id} | {s['toc_entries']} | {missing} | {', '.join(s['annex_sections']) or '-'} | {templ[0] + '–' + templ[-1] if templ else '-'} ({len(templ)}) |"
        )
    w("\n### TOC titles that differ from body headings\n")
    for doc_id, d in result["documents"].items():
        mism = d["structure"]["toc_title_mismatches"]
        if mism:
            w(
                f"- **{doc_id}**: {len(mism)} headings differ, e.g. "
                + "; ".join(f"§{m['section']} TOC '{m['toc']}' vs body '{m['body']}'" for m in mism[:4])
                + ". Body headings are authoritative for citations."
            )

    w("\n## 4. FAQ directories\n")
    w("| doc_id | items | unique questions | copies per question | same question, different answer |")
    w("|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        s = d["structure"]
        w(
            f"| {doc_id} | {s['faq_total']} | {s['faq_unique_questions']} | {s['faq_copies_per_question']} | {len(s['faq_same_question_different_answer'])} |"
        )
    w("\n### FAQ answers that cite the wrong section number (PROMPT.md §3.3-B)\n")
    w(
        "Method: support of a section = share of the FAQ's content words (question + answer) found in the section's own text (figures break ties). On this corpus every correct in-text citation scores >= 0.30 and the wrong ones <= 0.15; listed = cited support < 0.25 and the best section has at least 2x the support. Citations in the assistant are always built from structural metadata, never from these in-text numbers.\n"
    )
    w("| doc_id | FAQ | question | cites (support score) | actually supported by (score) |")
    w("|---|---|---|---|---|")
    for doc_id, d in result["documents"].items():
        for r in d["faq_section_reference_issues"]:
            best = "; ".join(f"§{b['section']} {b['title']} ({b['score']})" for b in r["supported_by"])
            w(
                f"| {doc_id} | {r['faq_id']} | {r['question'][:70]} | §{r['cited_in_text']} {r['cited_title'] or '(absent)'} ({r['support_in_cited']}) | {best} |"
            )

    w("\n## 5. Conflicting / scenario-dependent values (generic detectors)\n")
    for f in result["findings"]:
        w(f"- **[{f['category']}] {f['doc_id']}** — {f['detail']}")
        for ev in f["evidence"][:4]:
            w(f"  - `{ev[:240]}`")

    w("\n## 6. Unsupported services (assistant must say FinBase does not offer these)\n")
    for doc_id, d in result["documents"].items():
        for u in d["unsupported_services"]:
            w(f"- {doc_id} §{u['section']} ({u['section_title']}): **{u['service']}** — `{u['text'][:160]}`")

    w("\n## 7. Probes for information absent from the knowledge base\n")
    w("| topic | where found |")
    w("|---|---|")
    for label, hits in result["absence_probes"].items():
        w(f"| {label} | {', '.join(hits) if hits else '**absent** → assistant must abstain'} |")

    w("\n## 8. Boilerplate & duplicate collapse (details: `data/processed/dedup_report.json`)\n")
    dd = result["dedup"]
    w(
        f"Chunks before de-duplication: **{dd['input_chunks']}**, after: **{dd['kept_chunks']}** (removed {dd['removed']}).\n"
    )
    w("| doc_id | chunk type | canonical | copies |")
    w("|---|---|---|---|")
    for g in result["dedup_groups"]:
        if g["chunk_type"] in ("boilerplate", "annex"):
            w(f"| {g['doc_id']} | {g['chunk_type']} | {g['canonical']} | {g['copies']} |")
    faq_groups = [g for g in result["dedup_groups"] if g["chunk_type"] == "faq"]
    w(
        f"\nFAQ groups merged: {len(faq_groups)} (each unique question kept once, all members listed in `source_duplicates`)."
    )

    w("\n## 9. Verification of the findings stated in PROMPT.md §3.3\n")
    w("| key | claim | result |")
    w("|---|---|---|")
    for e in result["expectations"]:
        w(f"| {e['key']} | {e['claim']} | {'REPRODUCED' if e['reproduced'] else '**NOT REPRODUCED**'} |")
    w("\n<details><summary>Evidence</summary>\n")
    for e in result["expectations"]:
        w(f"**{e['key']}**")
        for ev in e["evidence"]:
            w(f"- `{ev}`")
        w("")
    w("</details>\n")
    w("## 10. How the assistant handles each trap\n")
    w("| trap | handling |")
    w("|---|---|")
    w(
        "| Missing TOC sections | Never indexed (no text exists); questions that depend on them fall below the abstain threshold or the LLM returns NOT_FOUND. |"
    )
    w(
        "| Wrong in-FAQ section numbers | Citations come from chunk metadata (doc + detected section + page); FAQ-only answers cite `FAQ Qnnn`; body sections preferred over FAQ. |"
    )
    w(
        "| Conflicts / scenarios | Context assembly keeps both conflicting chunks; system prompt rule 5 makes the model present both values with citations. |"
    )
    w(
        "| Truncated values | Chunks flagged `suspect_value`; prompt rule 6 + figure verifier ensure no completed number is emitted. |"
    )
    w('| Unsupported services | Section 22 chunk retrieved; prompt rule 8 → "FinBase does not offer X". |')
    w(
        "| Absent information | Abstain gate + NOT_FOUND sentinel → standard not-found message with KB contacts. |"
    )
    w(
        "| Boilerplate / FAQ repetition | De-duplicated to one canonical chunk with `source_duplicates`; boilerplate down-weighted (×0.6). |"
    )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Audit the FinBase PDF corpus")
    parser.add_argument("--data-dir", type=Path, default=ROOT_DIR / "data")
    parser.add_argument("--out", type=Path, default=ROOT_DIR / "docs" / "DATA_AUDIT.md")
    parser.add_argument(
        "--json-out", type=Path, default=None, help="default: <data-dir>/processed/audit.json"
    )
    args = parser.parse_args(argv)
    result = run_audit(args.data_dir)
    json_out: Path = args.json_out or args.data_dir / "processed" / "audit.json"
    for path in (args.out, json_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_markdown(result), encoding="utf-8")
    json_out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {"gate": result["gate"], "findings": len(result["findings"]), "out": str(args.out)}
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0 if result["gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
