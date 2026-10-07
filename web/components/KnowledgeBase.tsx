"use client";

import { useEffect, useState } from "react";

import { apiConfigured, getDocs } from "@/lib/api";

type Docs = Awaited<ReturnType<typeof getDocs>>["documents"];

export function KnowledgeBase() {
  const [docs, setDocs] = useState<Docs | null>(null);
  const [error, setError] = useState<string | null>(apiConfigured() ? null : "API not configured");
  useEffect(() => {
    if (apiConfigured()) getDocs().then((d) => setDocs(d.documents)).catch((e: Error) => setError(e.message));
  }, []);
  return (
    <section>
      <h2 className="text-base font-semibold text-ink">Knowledge base coverage</h2>
      {error && <p className="mt-2 text-sm text-muted">Coverage unavailable: {error}</p>}
      {docs && (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {docs.map((doc) => (
            <li key={doc.doc_id} className="rounded-xl border border-line bg-surface p-3">
              <p className="text-sm font-semibold text-ink">{doc.title}</p>
              <p className="font-mono text-xs text-muted">{doc.code}</p>
              <p className="mt-1 text-xs text-ink-2">{doc.sections.length} indexed sections</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
