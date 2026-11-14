"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Status } from "@/components/Status";
import { api, useApi } from "@/lib/api";
import type { RequestSummary } from "@/lib/types";

const EXAMPLE = "40 litres of lactose-free milk in 1 l cartons, delivered Tuesday";

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
        <textarea className="h-24 w-full rounded border border-neutral-300 p-3" placeholder={EXAMPLE}
          value={text} onChange={(e) => setText(e.target.value)} required />
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
