// Browser client for the FastAPI backend. The browser talks to Render directly (no proxy, no secrets).
import { readSSE } from "./sse";
import type { ChatResult, ChunkDetail, Health, HistoryTurn, MetaEvent, Source, Verification } from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

export function apiConfigured(): boolean {
  return API_URL.length > 0;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryable: boolean,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  if (!apiConfigured()) throw new ApiError("NEXT_PUBLIC_API_URL is not configured", 0, false);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init?.timeoutMs ?? 20000);
  try {
    const response = await fetch(`${API_URL}${path}`, { ...init, signal: init?.signal ?? controller.signal });
    if (!response.ok) throw await toError(response);
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("Network error — the API may be waking up", 0, true);
  } finally {
    clearTimeout(timer);
  }
}

async function toError(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  try {
    const body = (await response.json()) as { detail?: unknown; error?: string };
    detail = typeof body.detail === "string" ? body.detail : (body.error ?? detail);
  } catch {
    /* non-JSON error body */
  }
  const retryable = response.status === 502 || response.status === 503 || response.status === 504;
  const message = response.status === 429 ? "You're sending messages too quickly. Please wait a moment." : detail;
  return new ApiError(message, response.status, retryable);
}

export const getHealth = (timeoutMs = 8000) => request<Health>("/api/health", { timeoutMs });
export const getChunk = (chunkId: string) => request<ChunkDetail>(`/api/chunks/${encodeURIComponent(chunkId)}`);
export const getEvalLatest = () => request<Record<string, unknown>>("/api/eval/latest");
export const getEvalRuns = () => request<{ runs: Record<string, unknown>[] }>("/api/eval/runs");
export const getDocs = () => request<{ documents: { doc_id: string; title: string; code: string; sections: { section_id: string; title: string; page_start: number; page_end: number }[] }[] }>("/api/docs/list");

export function sendFeedback(requestId: string, rating: "up" | "down") {
  return request<{ ok: boolean }>("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId, rating }),
  });
}

export interface StreamHandlers {
  onMeta?: (meta: MetaEvent) => void;
  onToken?: (text: string) => void;
  onSources?: (payload: { sources: Source[]; related_sources: Source[] }) => void;
  onVerification?: (payload: Verification) => void;
  onDone?: (result: ChatResult) => void;
  onError?: (message: string) => void;
}

/** POST /api/chat with stream=true and dispatch SSE events. Abort with `signal` (Stop generating). */
export async function streamChat(
  body: { message: string; history: HistoryTurn[]; session_id?: string },
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  if (!apiConfigured()) throw new ApiError("NEXT_PUBLIC_API_URL is not configured", 0, false);
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError("Network error — the API may be waking up", 0, true);
  }
  if (!response.ok || !response.body) throw await toError(response);
  await readSSE(response.body, ({ event, data }) => {
    const payload: unknown = JSON.parse(data);
    switch (event) {
      case "meta":
        handlers.onMeta?.(payload as MetaEvent);
        break;
      case "token":
        handlers.onToken?.(payload as string);
        break;
      case "sources":
        handlers.onSources?.(payload as { sources: Source[]; related_sources: Source[] });
        break;
      case "verification":
        handlers.onVerification?.(payload as Verification);
        break;
      case "done":
        handlers.onDone?.(payload as ChatResult);
        break;
      case "error":
        handlers.onError?.((payload as { message?: string }).message ?? "Something went wrong.");
        break;
    }
  });
}
