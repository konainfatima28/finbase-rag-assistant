"use client";

import { AlertTriangle, CheckCircle2, CircleHelp, GitCompareArrows, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";

import type { Confidence } from "@/lib/types";

function Pill({ tone, icon, children, title }: { tone: "good" | "warn" | "bad" | "neutral"; icon: ReactNode; children: ReactNode; title?: string }) {
  const tones = {
    good: "bg-good-soft text-good",
    warn: "bg-warn-soft text-warn",
    bad: "bg-bad-soft text-bad",
    neutral: "bg-surface-2 text-ink-2",
  } as const;
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      <span aria-hidden="true">{icon}</span>
      {children}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const tone = confidence.label === "High" ? "good" : confidence.label === "Medium" ? "warn" : "bad";
  const icon = confidence.label === "High" ? <CheckCircle2 size={13} /> : <CircleHelp size={13} />;
  return (
    <Pill tone={tone} icon={icon} title="Based on retrieval strength, citation coverage and checks of every figure against the sources">
      {confidence.label} confidence
    </Pill>
  );
}

export function UnverifiedBadge({ figures }: { figures: string[] }) {
  if (!figures.length) return null;
  return (
    <Pill tone="warn" icon={<AlertTriangle size={13} />} title={`Not found in the cited sources: ${figures.join(", ")}`}>
      Some figures could not be verified
    </Pill>
  );
}

export function ConflictBadge() {
  return (
    <Pill tone="warn" icon={<GitCompareArrows size={13} />} title="FinBase documents state different values for this item">
      Documents differ — confirm with support
    </Pill>
  );
}

export function UngroundedBadge() {
  return (
    <Pill tone="bad" icon={<ShieldAlert size={13} />} title="The answer did not cite any source">
      No source citation
    </Pill>
  );
}

export function SourceTypeBadge({ type }: { type: string }) {
  const label = type === "faq" ? "FAQ" : type === "table" || type === "table_row" ? "Table" : type === "annex" ? "Annex" : "Policy";
  return <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</span>;
}
