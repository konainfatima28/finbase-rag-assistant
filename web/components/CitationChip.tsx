"use client";

/** Inline [n] marker in the answer; opens the evidence for source n. */
export function CitationChip({ n, label, onClick }: { n: number; label?: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label ? `Source ${n}: ${label}` : `Show source ${n}`}
      title={label}
      className="mx-0.5 inline-flex h-5 min-w-5 -translate-y-px items-center justify-center rounded-md bg-brand-soft px-1 align-middle text-[11px] font-semibold text-brand transition hover:bg-brand hover:text-brand-ink"
    >
      {n}
    </button>
  );
}
