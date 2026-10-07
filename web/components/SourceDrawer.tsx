"use client";

import { AlertTriangle, GitCompareArrows, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { getChunk } from "@/lib/api";
import type { ChunkDetail, Source } from "@/lib/types";

import { pages, sourceLabel } from "./SourcesPanel";

const FOCUSABLE = 'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])';

function StatusNote({ source }: { source: Source }) {
  if (source.status === "conflicting_sources") {
    return (
      <p className="mt-3 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-ink-2">
        <GitCompareArrows size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />
        This source states a different value for this item than another FinBase source. The answer shows both; please confirm with FinBase support.
      </p>
    );
  }
  const rows = source.unclear_rows ?? [];
  if (source.status === "unclear_value" || rows.length) {
    return (
      <p className="mt-3 flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-ink-2">
        <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />
        <span>
          Incomplete value in this source
          {rows.length ? `: ${rows.map((r) => (r.column ? `${r.column} for ${r.row}` : r.row)).join("; ")}` : ""}. The exact figure can&apos;t be safely determined.
        </span>
      </p>
    );
  }
  return null;
}

/** Evidence drawer: the full source with the cited snippet highlighted. Render with a key per source so state resets. */
export function SourceDrawer({ source, onClose }: { source: Source | null; onClose: () => void }) {
  const [chunk, setChunk] = useState<ChunkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    getChunk(source.chunk_id)
      .then((detail) => !cancelled && setChunk(detail))
      .catch(() => !cancelled && setError("Could not load the full source right now."));
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const items = [...panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKey);
      opener?.focus(); // return focus to the chip that opened the drawer
    };
  }, [source, onClose]);

  if (!source) return null;
  const text = chunk?.text ?? "";
  const snippet = source.snippet.replace(/…$/, "");
  const at = snippet ? text.indexOf(snippet) : -1;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose} role="presentation">
      <aside
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        className="flex h-full w-full max-w-xl flex-col bg-surface shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start gap-3 border-b border-line p-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{source.n > 0 ? `Source ${source.n}` : "Related topic"}</p>
            <h2 id="drawer-title" className="mt-0.5 text-base font-semibold text-ink">
              {sourceLabel(source)}
            </h2>
            <p className="mt-0.5 text-xs text-muted">
              {source.doc_title} · {source.doc_code} · {pages(source)}
            </p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close source" className="rounded-lg p-1.5 text-muted hover:bg-surface-2">
            <X size={18} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          {source.faq_question && <p className="text-sm font-medium text-ink">Q: {source.faq_question}</p>}
          <StatusNote source={source} />
          {error && <p className="mt-3 text-sm text-bad">{error}</p>}
          {!chunk && !error && (
            <div className="mt-3 space-y-2" aria-busy="true">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-4 animate-pulse rounded bg-surface-2" />
              ))}
            </div>
          )}
          {chunk && (
            <>
              <p className="mb-2 mt-3 text-xs text-muted">{chunk.breadcrumb}</p>
              <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-ink-2">
                {at >= 0 ? (
                  <>
                    {text.slice(0, at)}
                    <mark className="rounded bg-highlight px-0.5 text-ink">{text.slice(at, at + snippet.length)}</mark>
                    {text.slice(at + snippet.length)}
                  </>
                ) : (
                  text
                )}
              </pre>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
