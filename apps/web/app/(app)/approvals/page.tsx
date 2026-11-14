"use client";
import { useState } from "react";
import { OptionCard } from "@/components/OptionCard";
import { api, useApi } from "@/lib/api";
import type { Approval } from "@/lib/types";

export default function ApprovalsPage() {
  const { data, error, reload } = useApi<Approval[]>("/approvals", 5000);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const decide = async (orderId: string, body: object) => {
    setBusy(orderId);
    setMsg(null);
    try {
      await api(`/approvals/${orderId}`, { method: "POST", body: JSON.stringify(body) });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));   // e.g. price moved since the quote
    }
    setBusy(null);
    reload();
  };

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Waiting for your approval</h1>
      {error && <p className="text-sm text-human">Could not load approvals: {error}</p>}
      {msg && <p className="text-sm text-human">{msg}</p>}
      {data?.length === 0 && <p className="text-neutral-500">Nothing to approve.</p>}
      {data?.map((a) => (
        <section key={a.order_id} className="space-y-3 rounded-lg border border-human/40 bg-white p-5">
          <div className="flex items-baseline justify-between gap-4">
            <h2 className="font-semibold">Order {a.order_id.slice(0, 8)} · delivery {a.delivery_date}</h2>
            <span className="text-xs text-neutral-500">held because {a.reasons.join(", ")}</span>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            {a.options.map((o, i) => (
              <OptionCard key={`${o.label}-${i}`} option={o} action={
                <button disabled={busy === a.order_id} onClick={() => decide(a.order_id, {
                  action: "approve", option_index: i,
                  // the API asks the signer to confirm the shown total when the price moved since the quote
                  ...([...a.reasons, ...o.reasons].includes("PRICE_MOVED") ? { expected_total_eur: String(o.total_eur) } : {}),
                })}
                  className="rounded bg-auto px-3 py-1 text-sm text-white disabled:opacity-60">Approve this option</button>
              } />
            ))}
          </div>
          <button disabled={busy === a.order_id} onClick={() => { const note = prompt("Reason (optional)"); if (note !== null) decide(a.order_id, { action: "reject", note: note || null }); }}
            className="rounded border border-blocked px-3 py-1 text-sm disabled:opacity-60">Reject all</button>
        </section>
      ))}
    </div>
  );
}
