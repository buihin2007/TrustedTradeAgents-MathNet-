"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Status } from "@/components/Status";
import { api, useApi } from "@/lib/api";
import type { RequestSummary } from "@/lib/types";

const EXAMPLES = [
  "20 kg chicken fillet for Friday",
  "Lunch for 80 people next Tuesday, nut-free",
  "Dinner for 40 tomorrow built around salmon",
  "What should I cook for 100 guests on Saturday?",
];

export default function RequestsPage() {
  const router = useRouter();
  const { data, error } = useApi<RequestSummary[]>("/requests", 5000);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
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
      <form onSubmit={submit} className="space-y-3 rounded-lg border border-neutral-200 bg-white p-5">
        <h1 className="text-lg font-semibold">What do you need?</h1>
        <p className="text-sm text-neutral-500">Name products, a dish, or just the occasion: the agent asks for what is missing and suggests dishes from what Valio Aimo has in stock.</p>
        <textarea className="h-24 w-full rounded border border-neutral-300 p-3" placeholder={EXAMPLES[0]}
          value={text} onChange={(e) => setText(e.target.value)} required
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) e.currentTarget.form?.requestSubmit(); }} />
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => setText(example)}
              className="rounded-full border border-neutral-200 bg-neutral-50 px-3 py-1 text-xs text-neutral-600 hover:border-auto/40 hover:text-auto">
              {example}
            </button>
          ))}
        </div>
        {sendError && <p className="text-sm text-human">{sendError}</p>}
        <button disabled={busy || !text.trim()} className="rounded bg-auto px-4 py-2 font-medium text-white disabled:opacity-60">
          {busy ? "Sending..." : "Send to agent"}
        </button>
      </form>

      <section>
        <h2 className="mb-3 font-semibold">Recent requests</h2>
        {error && <p className="text-sm text-human">Could not load requests: {error}</p>}
        <ul className="divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white">
          {data?.length === 0 && <li className="p-4 text-sm text-neutral-500">No requests yet.</li>}
          {data?.map((r) => (
            <li key={r.id}>
              <Link href={`/requests/${r.id}`} className="flex items-center gap-4 p-4 hover:bg-neutral-50">
                <Status value={r.status} />
                <span className="flex-1 truncate">{r.text}</span>
                <span className="text-xs text-neutral-500">{new Date(r.created_at).toLocaleString()}</span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
