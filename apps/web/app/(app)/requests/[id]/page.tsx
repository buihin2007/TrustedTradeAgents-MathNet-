"use client";
import Link from "next/link";
import { use, useState } from "react";
import { OptionCard } from "@/components/OptionCard";
import { Status } from "@/components/Status";
import { api, eur, useApi } from "@/lib/api";
import type { RequestDetail } from "@/lib/types";

const DONE = ["placed", "blocked", "rejected", "failed"];

export default function RequestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [stopPolling, setStopPolling] = useState(false);
  const { data: r, error, reload } = useApi<RequestDetail>(`/requests/${id}`, stopPolling ? undefined : 2000);
  const [answer, setAnswer] = useState("");
  if (r && DONE.includes(r.status) && !stopPolling) setStopPolling(true);

  const send = async (value: string) => {
    await api(`/requests/${id}/answer`, { method: "POST", body: JSON.stringify({ answer: value }) });
    setAnswer("");
    setStopPolling(false);
    reload();
  };

  if (error) return <p className="text-human">Could not load request: {error}</p>;
  if (!r) return <p className="text-neutral-500">Loading...</p>;
  const chips = Object.entries(r.parsed ?? {}).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length));

  return (
    <div className="space-y-6">
      <Link href="/requests" className="text-sm text-neutral-500">← Requests</Link>
      <div className="flex items-start gap-3">
        <h1 className="flex-1 text-xl font-semibold">{r.text}</h1>
        <Status value={r.status} />
      </div>
      {r.status === "running" && <p className="text-sm text-neutral-500">Agent is working: {r.step ?? "starting"}...</p>}
      {r.error && <p className="text-sm text-human">{r.error}</p>}

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {chips.map(([k, v]) => (
            <span key={k} className="rounded bg-neutral-100 px-2 py-1 text-xs">
              {k.replaceAll("_", " ")}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
            </span>
          ))}
        </div>
      )}

      {r.question && r.status === "asking" && (
        <div className="space-y-3 rounded-lg border border-human/40 bg-human/5 p-4">
          <p className="font-medium">{r.question.question}</p>
          <div className="flex flex-wrap gap-2">
            {r.question.choices.map((c) => (
              <button key={c} onClick={() => send(c)} className="rounded border border-human/40 bg-white px-3 py-1 text-sm hover:bg-human/10">
                {c}
              </button>
            ))}
          </div>
          <form onSubmit={(e) => { e.preventDefault(); if (answer.trim()) send(answer); }} className="flex gap-2">
            <input className="flex-1 rounded border border-neutral-300 px-3 py-1 text-sm" placeholder="Or type your own answer"
              value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <button className="rounded bg-human px-3 py-1 text-sm text-white">Send</button>
          </form>
        </div>
      )}

      {r.order && (
        <div className="rounded-lg border border-neutral-200 bg-white p-4 text-sm">
          Order <span className="font-mono">{r.order.order_id.slice(0, 8)}</span>: <Status value={r.order.status} />{" "}
          {r.order.total_eur != null && <>total {eur(r.order.total_eur)}, </>}delivery {r.order.delivery_date}
          {r.order.reasons.length > 0 && <span className="text-neutral-500"> ({r.order.reasons.join(", ")})</span>}
          {r.order.status === "held" && <> · <Link href="/approvals" className="text-human underline">Review in Approvals</Link></>}
        </div>
      )}

      {r.options.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {r.options.map((o, i) => <OptionCard key={o.label} option={o} chosen={r.order?.chosen_index === i} />)}
        </div>
      )}
    </div>
  );
}
