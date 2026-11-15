"use client";
import { useState } from "react";
import { Icon, Spinner } from "@/components/Icon";
import { OptionCard } from "@/components/OptionCard";
import { Reasons } from "@/components/Reasons";
import { api, eur, useApi } from "@/lib/api";
import { day } from "@/lib/labels";
import type { Approval } from "@/lib/types";

type Note = { tone: "ok" | "error"; text: string };

export default function ApprovalsPage() {
  const { data, error, reload } = useApi<Approval[]>("/approvals", 5000);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const decide = async (orderId: string, body: { action: "approve" | "reject" } & Record<string, unknown>, total?: string) => {
    setBusy(orderId);
    setNote(null);
    try {
      await api(`/approvals/${orderId}`, { method: "POST", body: JSON.stringify(body) });
      setNote(body.action === "approve"
        ? { tone: "ok", text: `Approved${total ? ` at ${total}` : ""}. The gateway re-checked the price and placed the order.` }
        : { tone: "ok", text: "Rejected. Nothing was ordered." });
      setRejecting(null);
      setReason("");
    } catch (e) {
      setNote({ tone: "error", text: e instanceof Error ? e.message : String(e) });   // e.g. price moved since the quote
    }
    setBusy(null);
    reload();
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Waiting for your approval</h1>
        <p className="mt-1 text-sm text-slate-600">Orders the gateway held because they go beyond what the agent may do alone.</p>
      </div>
      {error && <p role="alert" className="text-sm text-human">Could not load approvals: {error}</p>}
      {note && (
        <p role={note.tone === "error" ? "alert" : "status"}
          className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${note.tone === "ok" ? "border-auto/30 bg-auto/5 text-auto" : "border-human/30 bg-human/5 text-human"}`}>
          <Icon name={note.tone === "ok" ? "check" : "alert"} className="mt-0.5" />{note.text}
        </p>
      )}
      {!data && !error && <div className="h-40 animate-pulse rounded-xl bg-slate-200" />}
      {data?.length === 0 && (
        <div className="card flex flex-col items-center gap-2 p-8 text-center">
          <Icon name="inbox" className="h-6 w-6 text-slate-400" />
          <p className="font-medium text-slate-800">Nothing to approve</p>
          <p className="text-sm text-slate-600">Orders above the agent&apos;s own limit, or with substitutes, will wait here for you.</p>
        </div>
      )}
      {data?.map((a) => {
        const working = busy === a.order_id;
        return (
          <section key={a.order_id} aria-labelledby={`order-${a.order_id}`} className="card space-y-4 border-human/40 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 id={`order-${a.order_id}`} className="font-semibold text-slate-900">
                  Order <span className="font-mono text-sm">{a.order_id.slice(0, 8)}</span>
                </h2>
                <p className="mt-0.5 flex items-center gap-1.5 text-sm text-slate-600">
                  <Icon name="calendar" className="h-3.5 w-3.5" />Delivery {day(a.delivery_date)}
                </p>
              </div>
              <div className="space-y-1 text-right">
                <p className="text-xs text-slate-600">Held because</p>
                <Reasons codes={a.reasons} end />
              </div>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {a.options.map((o, i) => (
                <OptionCard key={`${o.label}-${i}`} option={o} action={
                  <button disabled={working} className="btn-primary w-full" onClick={() => decide(a.order_id, {
                    action: "approve", option_index: i,
                    // the API asks the signer to confirm the shown total when the price moved since the quote
                    ...([...a.reasons, ...o.reasons].includes("PRICE_MOVED") ? { expected_total_eur: String(o.total_eur) } : {}),
                  }, eur(o.total_eur))}>
                    {working ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : <Icon name="check" />}
                    Approve this option
                  </button>
                } />
              ))}
            </div>
            {rejecting === a.order_id ? (
              <form className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3"
                onSubmit={(e) => { e.preventDefault(); decide(a.order_id, { action: "reject", note: reason.trim() || null }); }}>
                <label htmlFor={`reason-${a.order_id}`} className="block text-sm font-medium text-slate-700">Reason for rejecting (optional)</label>
                <input id={`reason-${a.order_id}`} className="field" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)}
                  placeholder="For example: we already have salmon" autoFocus />
                <div className="flex gap-2">
                  <button disabled={working} className="btn-dark"><Icon name="x" />Reject the whole order</button>
                  <button type="button" className="btn-ghost" onClick={() => setRejecting(null)}>Keep it</button>
                </div>
              </form>
            ) : (
              <button disabled={working} onClick={() => { setRejecting(a.order_id); setReason(""); }} className="btn-ghost">
                <Icon name="x" />Reject all
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
