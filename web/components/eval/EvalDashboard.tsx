"use client";

import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

import { apiConfigured, getEvalLatest, getEvalRuns } from "@/lib/api";

import { BarList } from "./BarList";

type Dict = Record<string, unknown>;
export interface Target {
  metric: string;
  value: number | null;
  target: number;
  op: ">=" | "<=" | "==";
  met: boolean | null;
}
export interface EvalRun {
  run_id?: string;
  created_at?: string;
  config?: Dict;
  summary?: Record<string, Record<string, number | null>>;
  targets?: Target[];
  by_category?: Record<string, Record<string, number | null>>;
  ablations?: Record<string, number | string | null>[];
  failures?: { id: string; category: string; question: string; expected: string; actual: string; reasons: string[] }[];
  notes?: string[];
}

export const fmt = (value: unknown, digits = 3): string => (typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—");

function TargetTile({ target }: { target: Target }) {
  const met = target.met;
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <p className="text-xs font-medium text-muted">{target.metric}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">{fmt(target.value)}</p>
      <p className={`mt-1 inline-flex items-center gap-1 text-xs font-medium ${met === null ? "text-muted" : met ? "text-good" : "text-bad"}`}>
        {met === null ? null : met ? <CheckCircle2 size={13} aria-hidden="true" /> : <XCircle size={13} aria-hidden="true" />}
        {met === null ? "not measured" : met ? "target met" : "target missed"} · target {target.op} {target.target}
      </p>
    </div>
  );
}

function Section({ title, children, note }: { title: string; children: React.ReactNode; note?: string }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {note && <p className="mt-0.5 text-xs text-muted">{note}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

export function EvalView({ run, runs }: { run: EvalRun; runs: Dict[] }) {
  const retrieval = run.summary?.retrieval ?? {};
  const kMetrics = ["hit@1", "recall@1", "recall@3", "recall@5", "recall@10", "mrr", "ndcg@10", "context_precision"].filter((k) => k in retrieval);
  const ablationMetrics = ["recall@5", "mrr", "ndcg@10"];
  const categories = Object.entries(run.by_category ?? {});
  const catColumns = Array.from(new Set(categories.flatMap(([, v]) => Object.keys(v)))).filter((c) => c !== "n");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold text-ink">Evaluation dashboard</h1>
        <p className="text-xs text-muted">
          run <span className="font-mono">{run.run_id ?? "?"}</span> · {run.created_at ?? ""} · {Object.entries(run.config ?? {}).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
        </p>
      </div>
      {run.notes?.length ? <ul className="list-disc space-y-1 pl-5 text-xs text-muted">{run.notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}

      {run.targets?.length ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {run.targets.map((t) => (
            <TargetTile key={t.metric} target={t} />
          ))}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Retrieval quality" note="Section-level match against gold sections (dedup-safe).">
          <BarList rows={kMetrics.map((k) => ({ label: k, value: retrieval[k] ?? null }))} max={1} />
        </Section>
        <Section title="Answer, grounding & safety">
          <BarList
            rows={[
              ["key-fact recall", run.summary?.answer?.key_fact_recall],
              ["LLM-judge (0–2 → 0–1)", run.summary?.answer?.judge_score_norm],
              ["groundedness (supported claims)", run.summary?.groundedness?.supported_claims],
              ["figure verified rate", run.summary?.groundedness?.figure_verified_rate],
              ["citation precision", run.summary?.citations?.precision],
              ["citation recall", run.summary?.citations?.recall],
              ["abstention F1", run.summary?.abstention?.f1],
            ].map(([label, value]) => ({ label: String(label), value: (value as number | null | undefined) ?? null }))}
            max={1}
          />
        </Section>
      </div>

      <Section title="Latency & cost" note="Per query, measured during the run.">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {Object.entries(run.summary?.system ?? {}).map(([k, v]) => (
            <div key={k} className="rounded-lg bg-surface-2 p-2.5">
              <p className="text-[11px] text-muted">{k}</p>
              <p className="text-base font-semibold tabular-nums text-ink">{fmt(v, k.includes("cost") ? 5 : 0)}</p>
            </div>
          ))}
        </div>
      </Section>

      {run.ablations?.length ? (
        <Section title="Ablations" note="One chart per metric (same scale), variants in fixed order.">
          <div className="grid gap-4 md:grid-cols-3">
            {ablationMetrics.map((metric) => (
              <div key={metric}>
                <p className="mb-1 text-xs font-semibold text-ink-2">{metric}</p>
                <BarList rows={run.ablations!.map((a) => ({ label: String(a.variant), value: typeof a[metric] === "number" ? (a[metric] as number) : null }))} max={1} compact />
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {categories.length ? (
        <Section title="Per-category breakdown">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">category</th>
                  <th className="py-1.5 pr-3 font-medium">n</th>
                  {catColumns.map((c) => (
                    <th key={c} className="py-1.5 pr-3 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums text-ink-2">
                {categories.map(([name, values]) => (
                  <tr key={name} className="border-t border-line">
                    <td className="py-1.5 pr-3 font-medium text-ink">{name}</td>
                    <td className="py-1.5 pr-3">{values.n ?? "—"}</td>
                    {catColumns.map((c) => (
                      <td key={c} className="py-1.5 pr-3">
                        {fmt(values[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}

      <Section title={`Failed cases (${run.failures?.length ?? 0})`} note="Expected vs actual for every item that missed a check.">
        {run.failures?.length ? (
          <ul className="space-y-2">
            {run.failures.map((f) => (
              <li key={f.id} className="rounded-lg border border-line p-2.5 text-xs">
                <p className="font-semibold text-ink">
                  <span className="font-mono">{f.id}</span> · {f.category} · {f.reasons.join(", ")}
                </p>
                <p className="mt-1 text-ink-2">Q: {f.question}</p>
                <p className="mt-1 text-muted">Expected: {f.expected}</p>
                <p className="mt-1 text-muted">Actual: {f.actual}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">No failed cases in this run.</p>
        )}
      </Section>

      {runs.length > 1 && (
        <Section title="Run comparison">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-muted">
                <tr>
                  {["file", "created", "mode", "recall@5", "mrr", "key-fact recall", "abstention F1"].map((h) => (
                    <th key={h} className="py-1.5 pr-3 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums text-ink-2">
                {runs.map((r) => {
                  const s = (r.summary ?? {}) as Record<string, Record<string, number>>;
                  const c = (r.config ?? {}) as Dict;
                  return (
                    <tr key={String(r.file)} className="border-t border-line">
                      <td className="py-1.5 pr-3 font-mono">{String(r.file)}</td>
                      <td className="py-1.5 pr-3">{String(r.created_at ?? "")}</td>
                      <td className="py-1.5 pr-3">{String(c.mode ?? "")}</td>
                      <td className="py-1.5 pr-3">{fmt(s.retrieval?.["recall@5"])}</td>
                      <td className="py-1.5 pr-3">{fmt(s.retrieval?.mrr)}</td>
                      <td className="py-1.5 pr-3">{fmt(s.answer?.key_fact_recall)}</td>
                      <td className="py-1.5 pr-3">{fmt(s.abstention?.f1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

export function EvalDashboard() {
  const [run, setRun] = useState<EvalRun | null>(null);
  const [runs, setRuns] = useState<Dict[]>([]);
  const [error, setError] = useState<string | null>(apiConfigured() ? null : "NEXT_PUBLIC_API_URL is not configured.");

  useEffect(() => {
    if (!apiConfigured()) return;
    getEvalLatest()
      .then((data) => setRun(data as EvalRun))
      .catch((e: Error) => setError(e.message));
    getEvalRuns()
      .then((data) => setRuns(data.runs))
      .catch(() => undefined);
  }, []);

  return (
    <div className="mx-auto max-w-6xl px-3 py-6 sm:px-4">
      {error && <p className="rounded-xl bg-warn-soft p-3 text-sm text-warn">Evaluation results unavailable: {error}</p>}
      {!run && !error && (
        <p className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Loading evaluation results…
        </p>
      )}
      {run && <EvalView run={run} runs={runs} />}
    </div>
  );
}
