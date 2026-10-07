"use client";

import { AlertTriangle, Check, Copy, GitCompareArrows, SearchX, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { followUpsFor } from "@/lib/conversation";
import type { ChatMessageView, ChatResult, Source } from "@/lib/types";

import { ConfidenceBadge, ConflictBadge, UngroundedBadge, UnverifiedBadge } from "./Badges";
import { RichAnswer } from "./RichAnswer";
import { SourcesPanel, sourceLabel } from "./SourcesPanel";

function time(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** One short notice per conflict / incomplete value, worded from the API's deterministic evidence status. */
function EvidenceNotices({ result }: { result: ChatResult }) {
  const evidence = result.evidence;
  if (!evidence) {
    return result.verification.warnings.includes("conflicting_sources") ? <ConflictBadge /> : null;
  }
  const unclear = [...new Map(evidence.unclear_values.map((u) => [`${u.column}|${u.row}`, u])).values()];
  if (!evidence.conflicts.length && !unclear.length) return null;
  return (
    <ul className="mt-3 space-y-1.5" aria-label="Notes about the sources">
      {evidence.conflicts.map((c) => (
        <li key={c.conflict_id} className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-ink-2">
          <GitCompareArrows size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />
          <span>
            <span className="font-medium text-ink">Sources differ.</span>{" "}
            {c.values.length > 1 ? `${c.description.replace(/\.$/, "")}: ${c.values.join(" vs ")}.` : c.description} Please confirm with FinBase support.
          </span>
        </li>
      ))}
      {unclear.map((u) => (
        <li key={`${u.column}|${u.row}`} className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-[13px] text-ink-2">
          <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden="true" />
          <span>
            <span className="font-medium text-ink">Incomplete value in the source.</span>{" "}
            {u.column && u.row ? `The ${u.column} for ${u.row}` : "This amount"} is truncated in FinBase&apos;s document, so the exact figure can&apos;t be confirmed.
          </span>
        </li>
      ))}
    </ul>
  );
}

export function AnswerCard({
  message,
  question,
  onAsk,
  onFeedback,
  onViewSource,
}: {
  message: ChatMessageView;
  question: string;
  onAsk: (q: string) => void;
  onFeedback: (message: ChatMessageView, rating: "up" | "down") => void;
  onViewSource: (source: Source) => void;
}) {
  const [copied, setCopied] = useState(false);
  const result = message.result;
  const streaming = message.status === "streaming";
  const text = result?.answer ?? message.content;
  // While streaming, the model's raw block numbers are not final citation numbers: hide them until `done`.
  const valid = result?.verification.citations_valid ?? [];
  const labels = Object.fromEntries((result?.sources ?? []).map((s) => [s.n, sourceLabel(s)]));
  const warnings = result?.verification.warnings ?? [];
  const notFound = Boolean(result && !result.answerable);

  const cite = (n: number) => {
    const source = result?.sources.find((s) => s.n === n);
    if (source) onViewSource(source);
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(result?.formatted ?? text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <article className="max-w-[95%] rounded-2xl rounded-bl-md border border-line bg-surface p-4 shadow-sm sm:max-w-[90%]" aria-busy={streaming}>
      {message.status === "error" ? (
        <p className="text-sm text-bad">{message.error ?? "Something went wrong."}</p>
      ) : streaming && !text ? (
        <div className="space-y-2" aria-label="Generating answer">
          <div className="h-3.5 w-11/12 animate-pulse rounded bg-surface-2" />
          <div className="h-3.5 w-9/12 animate-pulse rounded bg-surface-2" />
          <div className="h-3.5 w-7/12 animate-pulse rounded bg-surface-2" />
        </div>
      ) : notFound && result && !result.degraded ? (
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-muted" aria-hidden="true">
            <SearchX size={16} />
          </span>
          <div className="min-w-0">
            <p className="font-semibold text-ink">Not in the knowledge base</p>
            <p className="mt-0.5 text-sm leading-relaxed text-ink-2">{text}</p>
          </div>
        </div>
      ) : (
        <div aria-live={streaming ? "polite" : "off"}>
          <RichAnswer text={text} validCitations={valid} onCite={cite} labels={labels} />
          {streaming && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse rounded-sm bg-brand align-middle" aria-hidden="true" />}
        </div>
      )}

      {result && (
        <>
          {result.answerable && <EvidenceNotices result={result} />}
          {result.answerable && (
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <ConfidenceBadge confidence={result.confidence} />
              <UnverifiedBadge figures={result.verification.unverified_figures} />
              {warnings.includes("ungrounded_no_citations") && <UngroundedBadge />}
            </div>
          )}
          <SourcesPanel sources={result.sources} title="Sources" onView={onViewSource} idPrefix={message.id} />
          <SourcesPanel sources={result.related_sources} title="Related topics" onView={onViewSource} idPrefix={`${message.id}-rel`} />
          {result.answerable && (
            <section aria-label="Suggested follow-up questions" className="mt-3 flex flex-wrap gap-2">
              {followUpsFor(result.sources, question).map((q) => (
                <button key={q} type="button" onClick={() => onAsk(q)} className="rounded-full border border-line px-3 py-1 text-xs text-ink-2 hover:border-brand hover:text-brand">
                  {q}
                </button>
              ))}
            </section>
          )}
        </>
      )}

      <footer className="mt-3 flex items-center gap-1 text-xs text-muted">
        <time dateTime={new Date(message.createdAt).toISOString()}>{time(message.createdAt)}</time>
        {result && (
          <>
            <span className="ml-auto" />
            <button type="button" onClick={copy} aria-label="Copy answer" className="rounded-md p-1.5 hover:bg-surface-2">
              {copied ? <Check size={15} /> : <Copy size={15} />}
            </button>
            <button type="button" onClick={() => onFeedback(message, "up")} aria-label="Helpful" aria-pressed={message.feedback === "up"} className={`rounded-md p-1.5 hover:bg-surface-2 ${message.feedback === "up" ? "text-good" : ""}`}>
              <ThumbsUp size={15} />
            </button>
            <button type="button" onClick={() => onFeedback(message, "down")} aria-label="Not helpful" aria-pressed={message.feedback === "down"} className={`rounded-md p-1.5 hover:bg-surface-2 ${message.feedback === "down" ? "text-bad" : ""}`}>
              <ThumbsDown size={15} />
            </button>
          </>
        )}
      </footer>
    </article>
  );
}
