"use client";
import { useEffect, useState } from "react";
import { api, eur, useApi } from "@/lib/api";
import type { MandateView } from "@/lib/types";

const FIELDS: [keyof MandateView, string][] = [
  ["auto_limit_eur", "Agent may buy alone up to (EUR)"],
  ["order_limit_eur", "One order may never exceed (EUR)"],
  ["weekly_budget_eur", "Weekly budget (EUR)"],
  ["price_tolerance_pct", "Price may move since the quote by (%)"],
];

export default function MandatePage() {
  const { data, error, reload } = useApi<MandateView>("/mandate");
  const [form, setForm] = useState<Record<string, string>>({});
  const [allowSub, setAllowSub] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!data) return;
    setForm(Object.fromEntries(FIELDS.map(([k]) => [k, String(data[k])])));
    setAllowSub(data.allow_substitution);
  }, [data]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("/mandate", { method: "PUT", body: JSON.stringify({ ...form, allow_substitution: allowSub }) });
      setMsg("Saved as a new mandate version.");
      reload();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  };
  const toggleAgent = async () => {
    await api(`/agent/${data?.agent_status === "paused" ? "resume" : "pause"}`, { method: "POST" });
    reload();
  };

  if (error) return <p className="text-human">Could not load mandate: {error}</p>;
  if (!data) return <p className="text-neutral-500">Loading...</p>;
  const paused = data.agent_status === "paused";

  return (
    <div className="max-w-xl space-y-6">
      <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white p-4">
        <div>
          <p className="font-medium">Purchasing agent is {paused ? "paused" : "active"}</p>
          <p className="text-sm text-neutral-500">Paused agents are blocked at the Valio Aimo gateway immediately.</p>
        </div>
        <button onClick={toggleAgent} className={`rounded px-3 py-1 text-sm text-white ${paused ? "bg-auto" : "bg-blocked"}`}>
          {paused ? "Resume" : "Pause agent"}
        </button>
      </div>

      <form onSubmit={save} className="space-y-4 rounded-lg border border-neutral-200 bg-white p-5">
        <h1 className="text-lg font-semibold">Mandate v{data.version}</h1>
        <p className="text-sm text-neutral-500">
          Signed {new Date(data.signed_at).toLocaleString()}. Orders up to {eur(data.auto_limit_eur)} go through
          automatically; above that they wait for you; above {eur(data.order_limit_eur)} they are blocked.
        </p>
        {FIELDS.map(([k, label]) => (
          <label key={k} className="block text-sm">
            {label}
            <input type="number" step="0.01" min="0" className="mt-1 w-full rounded border border-neutral-300 px-3 py-2"
              value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} required />
          </label>
        ))}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={allowSub} onChange={(e) => setAllowSub(e.target.checked)} />
          Allow substitutes (they still need my approval)
        </label>
        {msg && <p className="text-sm text-neutral-600">{msg}</p>}
        <button className="rounded bg-auto px-4 py-2 font-medium text-white">Sign new version</button>
      </form>
    </div>
  );
}
