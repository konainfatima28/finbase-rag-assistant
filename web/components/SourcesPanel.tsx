"use client";

import { AlertTriangle, FileText, GitCompareArrows } from "lucide-react";

import type { Source } from "@/lib/types";

/** Human-readable reference: the API's canonical label, or one built from metadata for older payloads. */
export function sourceLabel(source: Source): string {
  if (source.label) return source.label;
  if (source.chunk_type === "faq" && source.faq_id) return `${source.doc_title} — FAQ ${source.faq_id}`;
  if (source.section_id === "0") return `${source.doc_title} — Document information`;
  return `${source.doc_title} — Section ${source.section_id}: ${source.section_title}`;
}

export function pages(source: Source): string {
  return source.page_end !== source.page_start ? `pp. ${source.page_start}–${source.page_end}` : `p. ${source.page_start}`;
}

function StatusIcon({ source }: { source: Source }) {
  if (source.status === "conflicting_sources") {
    return (
      <span className="inline-flex shrink-0 items-center text-warn" title="States a different value from another source">
        <GitCompareArrows size={13} aria-hidden="true" />
        <span className="sr-only">(conflicting value)</span>
      </span>
    );
  }
  if (source.status === "unclear_value" || source.quality_flag === "suspect_value") {
    return (
      <span className="inline-flex shrink-0 items-center text-warn" title="Contains an incomplete value">
        <AlertTriangle size={13} aria-hidden="true" />
        <span className="sr-only">(incomplete value)</span>
      </span>
    );
  }
  return null;
}

/** One compact, clickable source reference; opens the evidence drawer. */
export function SourceChip({ source, onView, idPrefix }: { source: Source; onView: (source: Source) => void; idPrefix: string }) {
  const label = sourceLabel(source);
  return (
    <li className="max-w-full">
      <button
        type="button"
        id={`${idPrefix}-src-${source.n}`}
        data-testid="source-chip"
        onClick={() => onView(source)}
        aria-label={`${source.n > 0 ? `Source ${source.n}: ` : ""}${label}, ${pages(source)}. Open evidence`}
        title={`${label} (${pages(source)})`}
        className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-1 text-left text-xs text-ink-2 transition hover:border-brand hover:text-ink"
      >
        {source.n > 0 && (
          <span className="inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded bg-brand-soft px-1 text-[10px] font-semibold text-brand">{source.n}</span>
        )}
        <span className="truncate">{label}</span>
        <StatusIcon source={source} />
      </button>
    </li>
  );
}

export function SourcesPanel({ sources, title, onView, idPrefix }: { sources: Source[]; title: string; onView: (source: Source) => void; idPrefix: string }) {
  if (!sources.length) return null;
  return (
    <section aria-label={title} className="mt-3">
      <h3 className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
        <FileText size={12} aria-hidden="true" /> {title}
      </h3>
      <ul className="flex flex-wrap gap-1.5">
        {sources.map((source) => (
          <SourceChip key={`${source.evidence_id ?? source.chunk_id}-${source.n}`} source={source} onView={onView} idPrefix={idPrefix} />
        ))}
      </ul>
    </section>
  );
}
