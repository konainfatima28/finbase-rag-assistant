"use client";

import { SendHorizontal, Square } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useState } from "react";

export const MAX_CHARS = 2000;

export function Composer({ busy, disabled, onSend, onStop }: { busy: boolean; disabled: boolean; onSend: (text: string) => void; onStop: () => void }) {
  const [text, setText] = useState("");
  const trimmed = text.trim();

  const submit = (event?: FormEvent) => {
    event?.preventDefault();
    if (!trimmed || busy || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form onSubmit={submit} className="rounded-2xl border border-line bg-surface p-2 shadow-sm focus-within:border-brand">
      <label htmlFor="composer" className="sr-only">
        Ask a question about FinBase products
      </label>
      <textarea
        id="composer"
        rows={2}
        maxLength={MAX_CHARS}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about loans, cards, savings, UPI, FDs or KYC…"
        className="block max-h-40 w-full resize-none bg-transparent px-2 py-1.5 text-[15px] text-ink placeholder:text-muted focus:outline-none"
      />
      <div className="flex items-center gap-2 px-1">
        <p className="text-[11px] text-muted">
          Enter to send · Shift+Enter for a new line · <span className="tabular-nums">{text.length}/{MAX_CHARS}</span>
        </p>
        <span className="ml-auto" />
        {busy ? (
          <button type="button" onClick={onStop} className="inline-flex items-center gap-1.5 rounded-xl border border-line px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2">
            <Square size={14} aria-hidden="true" /> Stop generating
          </button>
        ) : (
          <button type="submit" disabled={!trimmed || disabled} aria-label="Send" className="inline-flex items-center gap-1.5 rounded-xl bg-brand px-3 py-1.5 text-sm font-semibold text-brand-ink disabled:opacity-40">
            <SendHorizontal size={15} aria-hidden="true" /> Send
          </button>
        )}
      </div>
    </form>
  );
}
