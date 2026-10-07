// Types mirroring the FastAPI response contract (app/generation/answer.py).

export type ChunkType = "policy" | "table" | "table_row" | "faq" | "boilerplate" | "annex";

export interface Source {
  n: number;
  chunk_id: string;
  doc_id: string;
  doc_title: string;
  doc_code: string;
  section_id: string;
  section_title: string;
  page_start: number;
  page_end: number;
  chunk_type: ChunkType;
  citation: string;
  snippet: string;
  /** Removed from the API (Phase 2): retrieval scores are not a meaningful percentage. */
  relevance?: number | null;
  role: "primary" | "secondary" | "related";
  faq_id: string | null;
  quality_flag: "ok" | "suspect_value";
  // Canonical evidence fields (Phase 2, docs/API.md). Optional so older payloads still type-check.
  evidence_id?: string;
  label?: string;
  product?: string;
  source_type?: "section" | "table" | "row" | "faq" | "annex";
  status?: EvidenceStatus;
  scope?: "in_scope" | "general" | "other_product" | "unspecified";
  cited?: boolean;
  chunk_ids?: string[];
  faq_question?: string | null;
  conflict_ids?: string[];
  unclear_rows?: UnclearRow[];
}

export type EvidenceStatus = "normal" | "unclear_value" | "conflicting_sources";

export interface UnclearRow {
  chunk_id: string;
  line: number;
  column: string | null;
  row: string;
}

export interface EvidenceConflict {
  conflict_id: string;
  status: "conflicting_sources";
  category: string;
  doc_id: string;
  doc_title: string;
  scope: "same_document" | "cross_document";
  same_document: boolean;
  members: string[];
  sections: string[];
  values: string[];
  evidence_ids: string[];
  citations: number[];
  description: string;
}

export interface Evidence {
  statuses: Exclude<EvidenceStatus, "normal">[];
  /** Canonical evidence (cited items have `n`; uncited status-bearing items have `n: null`). */
  items: (Omit<Source, "n"> & { n: number | null })[];
  claims: { text: string; citations: number[]; evidence_ids: string[] }[];
  unclear_values: (UnclearRow & { evidence_id: string; n: number | null })[];
  conflicts: EvidenceConflict[];
}

export interface Confidence {
  score: number;
  label: "High" | "Medium" | "Low";
  retrieval?: number;
  citation_coverage?: number;
  verified_rate?: number;
}

export interface Verification {
  citations_valid: number[];
  invalid_markers: number[];
  citation_coverage: number;
  unverified_figures: string[];
  repaired_truncations: string[];
  warnings: string[];
}

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: Record<string, number>;
  model?: string;
}

export interface ChatResult {
  request_id: string;
  answer: string;
  formatted: string;
  answerable: boolean;
  abstain_reason?: string | null;
  sources: Source[];
  related_sources: Source[];
  confidence: Confidence;
  verification: Verification;
  usage: Usage;
  rewritten_query: string | null;
  language: string;
  notices: { pii: boolean; injection: boolean };
  conflicts: { doc_id: string; category: string; members: string[] }[];
  evidence?: Evidence;
  cached: boolean;
  degraded: boolean;
}

export interface MetaEvent {
  request_id: string;
  rewritten_query: string | null;
  language: string;
  notices: { pii: boolean; injection: boolean };
}

export interface ChunkDetail {
  chunk_id: string;
  doc_title: string;
  doc_code: string;
  section_id: string;
  section_title: string;
  breadcrumb: string;
  page_start: number;
  page_end: number;
  chunk_type: ChunkType;
  text: string;
  citation: string;
  quality_flag: string;
  source_duplicates: string[];
}

export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export interface ChatMessageView {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status: "streaming" | "done" | "error";
  result?: ChatResult;
  meta?: MetaEvent;
  error?: string;
  feedback?: "up" | "down";
}

export interface Health {
  status: string;
  provider: string;
  chat_model: string;
  embed_model: string;
  chunks: number;
  reranker: string;
  gate_calibrated: boolean;
}
