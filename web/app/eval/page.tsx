import type { Metadata } from "next";

import { EvalDashboard } from "@/components/eval/EvalDashboard";

export const metadata: Metadata = { title: "Evaluation · FinBase Support Assistant" };

export default function EvalPage() {
  return <EvalDashboard />;
}
