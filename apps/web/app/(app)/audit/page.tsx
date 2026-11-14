"use client";
import { useState } from "react";
import { Status } from "@/components/Status";
import { useApi } from "@/lib/api";
import type { AuditEvent } from "@/lib/types";

const LANGFUSE = process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_URL;
const DECISIONS = ["", "buy_auto", "ask_user", "approval_required", "approved", "rejected", "blocked"];

export default function AuditPage() {
  const [decision, setDecision] = useState("");
  const [requestId, setRequestId] = useState("");
  const qs = new URLSearchParams({ ...(decision && { decision }), ...(requestId && { request_id: requestId }) });
  const { data, error } = useApi<AuditEvent[]>(`/audit?${qs}`, 5000);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Audit trail</h1>
      <p className="text-sm text-neutral-500">Every decision the agent made for you. Append-only and hash-chained.</p>
      <div className="flex gap-3 text-sm">
        <select value={decision} onChange={(e) => setDecision(e.target.value)} className="rounded border border-neutral-300 px-2 py-1">
          {DECISIONS.map((d) => <option key={d} value={d}>{d ? d.replaceAll("_", " ") : "All decisions"}</option>)}
        </select>
        <input value={requestId} onChange={(e) => setRequestId(e.target.value.trim())} placeholder="Filter by request id"
          className="w-80 rounded border border-neutral-300 px-2 py-1" />
      </div>
      {error && <p className="text-sm text-human">Could not load audit trail: {error}</p>}
      <table className="w-full overflow-hidden rounded-lg border border-neutral-200 bg-white text-sm">
        <thead className="bg-neutral-100 text-left text-xs text-neutral-600">
          <tr><th className="p-2">Time</th><th className="p-2">Actor</th><th className="p-2">Event</th>
            <th className="p-2">Decision</th><th className="p-2">Summary</th><th className="p-2">Trace</th></tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {data?.map((e) => (
            <tr key={e.seq}>
              <td className="whitespace-nowrap p-2 text-neutral-500">{new Date(e.ts).toLocaleTimeString()}</td>
              <td className="p-2">{e.actor_type}</td>
              <td className="p-2 font-mono text-xs">{e.event_type}</td>
              <td className="p-2">{e.decision !== "none" && <Status value={e.decision} />}</td>
              <td className="p-2">{e.summary}</td>
              <td className="p-2">
                {e.trace_id && (LANGFUSE
                  ? <a className="text-auto underline" target="_blank" href={`${LANGFUSE}/traces/${e.trace_id}`}>open</a>
                  : <span className="font-mono text-xs text-neutral-400">{e.trace_id.slice(0, 8)}</span>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data?.length === 0 && <p className="text-sm text-neutral-500">No events yet.</p>}
    </div>
  );
}
