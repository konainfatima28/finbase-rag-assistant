"use client";

import { Loader2, ServerCrash, Settings2, ShieldCheck } from "lucide-react";

export type ApiStatus = "unconfigured" | "checking" | "waking" | "online" | "offline";

export function StatusBanner({ status, onRetry, pii }: { status: ApiStatus; onRetry: () => void; pii: boolean }) {
  return (
    <div className="space-y-2" role="status" aria-live="polite">
      {status === "unconfigured" && (
        <Banner tone="warn" icon={<Settings2 size={16} />}>
          The API URL is not configured. Set <code className="font-mono">NEXT_PUBLIC_API_URL</code> to the Render API URL and rebuild.
        </Banner>
      )}
      {status === "waking" && (
        <Banner tone="info" icon={<Loader2 size={16} className="animate-spin" />}>
          The API is waking up (free hosting sleeps when idle — this can take 30–60 seconds). Your question will be sent automatically.
        </Banner>
      )}
      {status === "offline" && (
        <Banner tone="bad" icon={<ServerCrash size={16} />}>
          The assistant is unreachable right now.{" "}
          <button type="button" onClick={onRetry} className="font-semibold underline">
            Retry
          </button>
        </Banner>
      )}
      {pii && (
        <Banner tone="info" icon={<ShieldCheck size={16} />}>
          For your safety, never share card numbers, CVV, OTP, PIN, passwords, Aadhaar or PAN. Sensitive details were masked before processing.
        </Banner>
      )}
    </div>
  );
}

function Banner({ tone, icon, children }: { tone: "info" | "warn" | "bad"; icon: React.ReactNode; children: React.ReactNode }) {
  const tones = { info: "bg-brand-soft text-ink-2", warn: "bg-warn-soft text-warn", bad: "bg-bad-soft text-bad" } as const;
  return (
    <div className={`flex items-start gap-2 rounded-xl px-3 py-2 text-sm ${tones[tone]}`}>
      <span aria-hidden="true" className="mt-0.5">
        {icon}
      </span>
      <p>{children}</p>
    </div>
  );
}
