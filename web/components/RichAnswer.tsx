"use client";

// XSS-safe renderer for model text: builds React elements only (never dangerouslySetInnerHTML).
// Supports paragraphs, "-"/"•"/"*" bullets, numbered lists, **bold**, and [n] citation chips.
import { Fragment, type ReactNode } from "react";

import { CitationChip } from "./CitationChip";

const TOKEN = /(\*\*[^*]+\*\*|\[\d{1,2}\])/g;

function renderInline(text: string, validCitations: Set<number>, onCite: (n: number) => void, keyPrefix: string, labels: Record<number, string>): ReactNode[] {
  return text.split(TOKEN).map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    const marker = /^\[(\d{1,2})\]$/.exec(part);
    if (marker) {
      const n = Number(marker[1]);
      return validCitations.has(n) ? <CitationChip key={key} n={n} label={labels[n]} onClick={() => onCite(n)} /> : null;
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={key}>{part}</Fragment>;
  });
}

export function RichAnswer({
  text,
  validCitations,
  onCite,
  labels = {},
}: {
  text: string;
  validCitations: number[];
  onCite: (n: number) => void;
  /** citation number -> human-readable source label (accessible name + tooltip of the inline chip) */
  labels?: Record<number, string>;
}) {
  const valid = new Set(validCitations);
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flush = () => {
    if (!list) return;
    const items = list.items.map((item, i) => (
      <li key={i} className="pl-1">
        {renderInline(item, valid, onCite, `li${blocks.length}-${i}`, labels)}
      </li>
    ));
    blocks.push(
      list.ordered ? (
        <ol key={blocks.length} className="my-2 list-decimal space-y-1 pl-5">
          {items}
        </ol>
      ) : (
        <ul key={blocks.length} className="my-2 list-disc space-y-1 pl-5">
          {items}
        </ul>
      ),
    );
    list = null;
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = /^(?:[-•*])\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flush();
        list = { ordered, items: [] };
      }
      list.items.push((bullet ?? numbered)![1]!);
      continue;
    }
    flush();
    if (line) {
      blocks.push(
        <p key={blocks.length} className="my-1.5 leading-relaxed">
          {renderInline(line, valid, onCite, `p${blocks.length}`, labels)}
        </p>,
      );
    }
  }
  flush();
  return <div className="text-[15px] text-ink">{blocks}</div>;
}
