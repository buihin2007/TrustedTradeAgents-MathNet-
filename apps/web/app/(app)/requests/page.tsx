"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Authority } from "@/components/Authority";
import { Icon, Spinner } from "@/components/Icon";
import { Status } from "@/components/Status";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/labels";
import type { MandateView, RequestSummary } from "@/lib/types";

const EXAMPLES = [
  "20 kg chicken fillet for Friday",
  "Lunch for 80 people next Tuesday, nut-free",
  "Dinner for 40 tomorrow built around salmon",
  "What should I cook for 100 guests on Saturday?",
];

export default function RequestsPage() {
  const router = useRouter();
  const { data, error } = useApi<RequestSummary[]>("/requests", 5000);
  const { data: mandate } = useApi<MandateView>("/mandate");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setSendError(null);
    try {
      const created = await api<RequestSummary>("/requests", { method: "POST", body: JSON.stringify({ text }) });
      router.push(`/requests/${created.id}`);
    } catch (err) {
      setSendError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-8">
      <form onSubmit={submit} className="card space-y-4 p-5 shadow-sm sm:p-6">
        <div>
          <label htmlFor="need" className="text-xl font-semibold text-slate-900">What do you need?</label>
          <p className="mt-1 text-sm text-slate-600">
            Name products, a dish, or just the occasion. The agent asks for what is missing and suggests dishes from what
            Valio Aimo has in stock on your delivery day.
          </p>
        </div>
        <textarea id="need" className="field h-28 resize-y text-base" placeholder={EXAMPLES[0]}
          value={text} onChange={(e) => setText(e.target.value)} required maxLength={2000}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit(); }} />
        <div className="flex flex-wrap gap-2" aria-label="Examples">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => setText(example)}
              className="flex min-h-8 items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-3 text-xs text-slate-700 transition-colors duration-200 hover:border-auto/40 hover:bg-auto/5 hover:text-auto">
              <Icon name="sparkles" className="h-3 w-3" />
              {example}
            </button>
          ))}
        </div>
        {sendError && (
          <p role="alert" className="flex items-start gap-2 text-sm text-human"><Icon name="alert" className="mt-0.5" />{sendError}</p>
        )}
        <div className="flex items-center gap-3">
          <button disabled={busy || !text.trim()} className="btn-primary">
            {busy ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : <Icon name="send" />}
            {busy ? "Sending" : "Send to agent"}
          </button>
          <span className="hidden text-xs text-slate-500 sm:inline">or press Ctrl + Enter</span>
        </div>
      </form>

      {mandate && <Authority auto={mandate.auto_limit_eur} limit={mandate.order_limit_eur} weekly={mandate.weekly_budget_eur} />}

      <section>
        <h2 className="mb-3 font-semibold text-slate-900">Recent requests</h2>
        {error && <p role="alert" className="text-sm text-human">Could not load requests: {error}</p>}
        {!data && !error && <div className="h-24 animate-pulse rounded-xl bg-slate-200" />}
        {data?.length === 0 && (
          <div className="card flex flex-col items-center gap-2 p-8 text-center">
            <Icon name="message" className="h-6 w-6 text-slate-400" />
            <p className="font-medium text-slate-800">No requests yet</p>
            <p className="text-sm text-slate-600">Pick an example above to see the agent at work.</p>
          </div>
        )}
        {!!data?.length && (
          <ul className="card divide-y divide-slate-100 overflow-hidden">
            {data.map((r) => (
              <li key={r.id}>
                <Link href={`/requests/${r.id}`} className="flex items-center gap-3 px-4 py-3 transition-colors duration-200 hover:bg-slate-50">
                  <span className="shrink-0 sm:w-44"><Status value={r.status} /></span>
                  <span className="min-w-0 flex-1 truncate text-slate-800">{r.text}</span>
                  <time dateTime={r.created_at} title={new Date(r.created_at).toLocaleString()} className="shrink-0 text-xs text-slate-500">
                    {ago(r.created_at)}
                  </time>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
