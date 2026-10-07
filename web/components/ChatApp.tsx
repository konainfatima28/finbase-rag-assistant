"use client";

import { MessageSquarePlus, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiConfigured, getHealth, sendFeedback, streamChat } from "@/lib/api";
import { DISCLAIMER, STARTERS, clearConversation, historyFrom, loadConversation, newId, saveConversation } from "@/lib/conversation";
import type { ChatMessageView, Source } from "@/lib/types";

import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";
import { SourceDrawer } from "./SourceDrawer";
import { type ApiStatus, StatusBanner } from "./StatusBanner";

const WAKE_POLL_MS = 3000;
const WAKE_GIVE_UP_MS = 120000;

export function ChatApp() {
  const [messages, setMessages] = useState<ChatMessageView[]>([]);
  const [restored, setRestored] = useState(false);
  const [status, setStatus] = useState<ApiStatus>(apiConfigured() ? "checking" : "unconfigured");
  const [busy, setBusy] = useState(false);
  const [drawer, setDrawer] = useState<Source | null>(null);
  const [piiNotice, setPiiNotice] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const pendingRef = useRef<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const sessionId = useRef<string>(newId().replace(/[^A-Za-z0-9_-]/g, ""));

  // Block bodies on purpose: an effect must return nothing or a cleanup function. Expression bodies leak
  // return values (current Chrome's scrollIntoView returns a Promise -> "destroy is not a function").
  // sessionStorage is read only after hydration (server and first client render both start empty), and
  // nothing is saved before it has been read: otherwise the initial empty list overwrites the stored
  // conversation (React StrictMode re-runs effects in dev, so the re-run load then read []).
  useEffect(() => {
    setMessages(loadConversation());
    setRestored(true);
  }, []);
  useEffect(() => {
    if (restored) saveConversation(messages);
  }, [messages, restored]);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const update = (id: string, patch: (m: ChatMessageView) => ChatMessageView) => setMessages((all) => all.map((m) => (m.id === id ? patch(m) : m)));

  const waitForApi = useCallback(async (): Promise<boolean> => {
    if (!apiConfigured()) return false;
    const started = Date.now();
    let first = true;
    while (Date.now() - started < WAKE_GIVE_UP_MS) {
      try {
        await getHealth(first ? 4000 : 10000);
        setStatus("online");
        return true;
      } catch {
        setStatus("waking");
        first = false;
        await new Promise((resolve) => setTimeout(resolve, WAKE_POLL_MS));
      }
    }
    setStatus("offline");
    return false;
  }, []);

  useEffect(() => {
    void waitForApi();
  }, [waitForApi]);

  const ask = useCallback(
    async (question: string) => {
      if (busy) return;
      const history = historyFrom(messages);
      const user: ChatMessageView = { id: newId(), role: "user", content: question, createdAt: Date.now(), status: "done" };
      const answer: ChatMessageView = { id: newId(), role: "assistant", content: "", createdAt: Date.now(), status: "streaming" };
      setMessages((all) => [...all, user, answer]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const run = async (): Promise<void> => {
        await streamChat(
          { message: question, history, session_id: sessionId.current },
          {
            onMeta: (meta) => {
              update(answer.id, (m) => ({ ...m, meta }));
              if (meta.notices.pii) setPiiNotice(true);
            },
            onToken: (token) => update(answer.id, (m) => ({ ...m, content: m.content + token })),
            onDone: (result) => update(answer.id, (m) => ({ ...m, status: "done", result, content: result.answer })),
            onError: (message) => update(answer.id, (m) => ({ ...m, error: message })),
          },
          controller.signal,
        );
      };

      try {
        await run();
        setStatus("online");
      } catch (error) {
        if ((error as Error).name === "AbortError") {
          update(answer.id, (m) => ({ ...m, status: "done", content: m.content ? `${m.content}\n\n_(stopped)_` : "Stopped." }));
        } else if (error instanceof ApiError && error.retryable) {
          pendingRef.current = question;
          if (await waitForApi()) {
            try {
              update(answer.id, (m) => ({ ...m, content: "" }));
              await run();
            } catch (retryError) {
              update(answer.id, (m) => ({ ...m, status: "error", error: (retryError as Error).message }));
            }
          } else {
            update(answer.id, (m) => ({ ...m, status: "error", error: "The assistant is unreachable. Please try again later." }));
          }
          pendingRef.current = null;
        } else {
          update(answer.id, (m) => ({ ...m, status: "error", error: (error as Error).message }));
        }
      } finally {
        setMessages((all) => all.map((m) => (m.id === answer.id && m.status === "streaming" ? { ...m, status: m.result ? "done" : "error", error: m.error ?? (m.result ? undefined : "The answer was interrupted.") } : m)));
        abortRef.current = null;
        setBusy(false);
      }
    },
    [busy, messages, waitForApi],
  );

  const stop = () => abortRef.current?.abort();

  const reset = () => {
    abortRef.current?.abort();
    clearConversation();
    setMessages([]);
    setPiiNotice(false);
    sessionId.current = newId().replace(/[^A-Za-z0-9_-]/g, "");
  };

  const feedback = (message: ChatMessageView, rating: "up" | "down") => {
    if (!message.result) return;
    update(message.id, (m) => ({ ...m, feedback: rating }));
    void sendFeedback(message.result.request_id, rating).catch(() => undefined);
  };

  const closeDrawer = useCallback(() => setDrawer(null), []);
  const questionFor = (index: number) => messages[index - 1]?.content ?? "";

  return (
    <div className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-3xl flex-col px-3 pb-4 sm:px-4">
      <div className="pt-3">
        <StatusBanner status={status} onRetry={() => void waitForApi()} pii={piiNotice} />
      </div>

      <div className="flex-1 py-4">
        {messages.length === 0 ? (
          <section className="mt-6 text-center sm:mt-12" aria-labelledby="welcome">
            <div className="mx-auto mb-3 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand">
              <Sparkles size={24} aria-hidden="true" />
            </div>
            <h1 id="welcome" className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              How can we help today?
            </h1>
            <p className="mx-auto mt-2 max-w-md text-sm text-muted">Answers come only from FinBase&apos;s official policy documents, with the exact section and page for every fact.</p>
            <ul className="mt-6 grid gap-2 text-left sm:grid-cols-2">
              {STARTERS.map((starter) => (
                <li key={starter.label}>
                  <button
                    type="button"
                    onClick={() => void ask(starter.question)}
                    disabled={status === "unconfigured"}
                    className="h-full w-full rounded-xl border border-line bg-surface p-3 text-left transition hover:border-brand disabled:opacity-50"
                  >
                    <span className="block text-xs font-semibold uppercase tracking-wide text-brand">{starter.label}</span>
                    <span className="mt-1 block text-sm text-ink-2">{starter.question}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <ol className="space-y-4" aria-label="Conversation">
            {messages.map((message, index) => (
              <li key={message.id} className={message.role === "user" ? "flex justify-end" : ""}>
                {message.role === "user" ? (
                  <div className="max-w-[85%] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-[15px] text-brand-ink shadow-sm">
                    <p className="whitespace-pre-wrap break-words">{message.content}</p>
                  </div>
                ) : (
                  <AnswerCard message={message} question={questionFor(index)} onAsk={(q) => void ask(q)} onFeedback={feedback} onViewSource={setDrawer} />
                )}
              </li>
            ))}
          </ol>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 bg-bg pb-1 pt-2">
        <div className="mb-2 flex items-center justify-between gap-3">
          <p className="text-[11px] text-muted">{DISCLAIMER}</p>
          {messages.length > 0 && (
            <button type="button" onClick={reset} className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1 text-xs font-medium text-ink-2 hover:bg-surface-2">
              <MessageSquarePlus size={14} aria-hidden="true" /> New chat
            </button>
          )}
        </div>
        <Composer busy={busy} disabled={status === "unconfigured"} onSend={(q) => void ask(q)} onStop={stop} />
      </div>
      <SourceDrawer key={drawer?.chunk_id ?? "none"} source={drawer} onClose={closeDrawer} />
    </div>
  );
}
