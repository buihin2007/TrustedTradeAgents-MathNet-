"use client";
import { useState } from "react";
import { Icon, type IconName } from "@/components/Icon";
import { Status } from "@/components/Status";
import { useApi } from "@/lib/api";
import { statusLabel } from "@/lib/labels";
import type { AuditEvent } from "@/lib/types";

const LANGFUSE = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_URL;
const DECISIONS = ["", "buy_auto", "ask_user", "approval_required", "approved", "rejected", "blocked"];
const ACTOR: Record<AuditEvent["actor_type"], { icon: IconName; label: string }> = {
  user: { icon: "user", label: "You" },
  agent: { icon: "bot", label: "Agent" },
  llm: { icon: "sparkles", label: "Model" },
  gateway: { icon: "shield", label: "Gateway" },
  system: { icon: "server", label: "System" },
};

export default function AuditPage() {
  const [decision, setDecision] = useState("");
  const [requestId, setRequestId] = useState("");
  const qs = new URLSearchParams({ ...(decision && { decision }), ...(requestId && { request_id: requestId }) });
  const { data, error } = useApi<AuditEvent[]>(`/audit?${qs}`, 5000);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Audit trail</h1>
        <p className="mt-1 text-sm text-slate-600">Every decision made on your behalf, by whom and why. Append-only and hash-chained.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="decision" className="mb-1 block text-xs font-medium text-slate-700">Decision</label>
          <select id="decision" value={decision} onChange={(e) => setDecision(e.target.value)} className="field w-52">
            {DECISIONS.map((d) => <option key={d} value={d}>{d ? statusLabel(d) : "All decisions"}</option>)}
          </select>
        </div>
        <div className="min-w-0 flex-1 sm:max-w-sm">
          <label htmlFor="request" className="mb-1 block text-xs font-medium text-slate-700">Request id</label>
          <input id="request" value={requestId} onChange={(e) => setRequestId(e.target.value.trim())} placeholder="Paste a request id"
            className="field font-mono" />
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-human">Could not load audit trail: {error}</p>}
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs font-medium text-slate-600">
            <tr>
              <th scope="col" className="px-3 py-2">Time</th>
              <th scope="col" className="px-3 py-2">Actor</th>
              <th scope="col" className="px-3 py-2">Event</th>
              <th scope="col" className="px-3 py-2">Decision</th>
              <th scope="col" className="px-3 py-2">Summary</th>
              <th scope="col" className="px-3 py-2">Trace</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.map((e) => {
              const actor = ACTOR[e.actor_type] ?? ACTOR.system;
              return (
                <tr key={e.seq} className="align-top hover:bg-slate-50">
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs tabular-nums text-slate-600" title={new Date(e.ts).toLocaleString("en-GB")}>
                    {new Date(e.ts).toLocaleTimeString("en-GB")}
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-slate-800">
                    <span className="inline-flex items-center gap-1.5"><Icon name={actor.icon} className="h-3.5 w-3.5 text-slate-500" />{actor.label}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-700">{e.event_type}</td>
                  <td className="px-3 py-2">{e.decision !== "none" && <Status value={e.decision} />}</td>
                  <td className="px-3 py-2 text-slate-800">{e.summary}</td>
                  <td className="px-3 py-2">
                    {e.trace_id && (LANGFUSE
                      ? <a className="inline-flex items-center gap-1 text-auto hover:underline" target="_blank" rel="noreferrer" href={`${LANGFUSE}/traces/${e.trace_id}`}>
                          Open<Icon name="external" className="h-3 w-3" /><span className="sr-only"> trace in Langfuse</span>
                        </a>
                      : <span className="font-mono text-xs text-slate-500">{e.trace_id.slice(0, 8)}</span>)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!data && !error && <div className="m-3 h-16 animate-pulse rounded-lg bg-slate-100" />}
        {data?.length === 0 && <p className="p-6 text-center text-sm text-slate-600">No events match these filters.</p>}
      </div>
    </div>
  );
}
