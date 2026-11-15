"use client";
import { useEffect, useState } from "react";
import { Authority } from "@/components/Authority";
import { Icon } from "@/components/Icon";
import { api, useApi } from "@/lib/api";
import type { MandateView } from "@/lib/types";

const FIELDS: { key: keyof MandateView; label: string; help: string; unit: "EUR" | "%" }[] = [
  { key: "auto_limit_eur", label: "Agent may buy alone up to", help: "Orders at or below this go through without asking you.", unit: "EUR" },
  { key: "order_limit_eur", label: "One order may never exceed", help: "Above this the gateway blocks the order, even with your approval.", unit: "EUR" },
  { key: "weekly_budget_eur", label: "Weekly budget", help: "Total of placed orders per week. Going over blocks the order.", unit: "EUR" },
  { key: "price_tolerance_pct", label: "Price may move since the quote by", help: "A larger move holds the order so you can confirm the new total.", unit: "%" },
];

type Note = { tone: "ok" | "error"; text: string };

export default function MandatePage() {
  const { data, error, reload } = useApi<MandateView>("/mandate");
  const [form, setForm] = useState<Record<string, string>>({});
  const [allowSub, setAllowSub] = useState(true);
  const [note, setNote] = useState<Note | null>(null);
  const [saving, setSaving] = useState(false);
  const [agentStatus, setAgentStatus] = useState<"active" | "paused">("active");

  useEffect(() => {
    if (!data) return;
    setForm(Object.fromEntries(FIELDS.map(({ key }) => [key, String(data[key])])));
    setAllowSub(data.allow_substitution);
    if (data.agent_status) setAgentStatus(data.agent_status);
  }, [data]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setNote(null);
    try {
      await api("/mandate", { method: "PUT", body: JSON.stringify({ ...form, allow_substitution: allowSub, allowed_category_codes: data?.allowed_category_codes ?? null }) });
      setNote({ tone: "ok", text: "Signed as a new mandate version. The gateway applies it from the next call." });
      reload();
    } catch (err) {
      setNote({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
    setSaving(false);
  };
  const toggleAgent = async () => {
    try {
      const res = await api<{ status: "active" | "paused" }>(`/agent/${agentStatus === "paused" ? "resume" : "pause"}`, { method: "POST" });
      setAgentStatus(res.status);
    } catch (err) {
      setNote({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
  };

  if (error) return <p role="alert" className="text-human">Could not load mandate: {error}</p>;
  if (!data) return <div className="h-64 max-w-2xl animate-pulse rounded-xl bg-slate-200" aria-busy="true" />;
  const paused = agentStatus === "paused";

  return (
    <div className="max-w-2xl space-y-6">
      <div className={`card flex flex-wrap items-center justify-between gap-4 p-4 ${paused ? "border-blocked" : ""}`}>
        <div className="flex items-center gap-3">
          <span className={`flex h-9 w-9 items-center justify-center rounded-full ${paused ? "bg-blocked text-white" : "bg-auto/10 text-auto"}`}>
            <Icon name={paused ? "pause" : "bot"} />
          </span>
          <div>
            <p className="font-medium text-slate-900">Purchasing agent is {paused ? "paused" : "active"}</p>
            <p className="text-sm text-slate-600">A paused agent is refused at the Valio Aimo gateway on its next call.</p>
          </div>
        </div>
        <button onClick={toggleAgent} className={paused ? "btn-primary" : "btn-dark"}>
          <Icon name={paused ? "play" : "pause"} />{paused ? "Resume agent" : "Pause agent"}
        </button>
      </div>

      <form onSubmit={save} className="card space-y-5 p-5 sm:p-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Mandate <span className="font-mono text-base text-slate-600">v{data.version}</span></h1>
          <p className="mt-1 text-sm text-slate-600">
            Signed {new Date(data.signed_at).toLocaleString("en-GB")}. Each change is signed as a new version; old versions stay in the audit trail.
          </p>
        </div>

        <Authority auto={form.auto_limit_eur ?? data.auto_limit_eur} limit={form.order_limit_eur ?? data.order_limit_eur} link={false} />

        <div className="grid gap-4 sm:grid-cols-2">
          {FIELDS.map(({ key, label, help, unit }) => (
            <div key={key}>
              <label htmlFor={key} className="mb-1 block text-sm font-medium text-slate-700">{label}</label>
              <div className="relative">
                <input id={key} type="number" step="0.01" min="0" inputMode="decimal" aria-describedby={`${key}-help`}
                  className="field pr-12 font-mono tabular-nums" value={form[key] ?? ""}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })} required />
                <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-slate-500">{unit}</span>
              </div>
              <p id={`${key}-help`} className="mt-1 text-xs text-slate-600">{help}</p>
            </div>
          ))}
        </div>

        <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-auto" checked={allowSub} onChange={(e) => setAllowSub(e.target.checked)} />
          <span>
            <span className="font-medium text-slate-900">Allow Valio substitutes</span>
            <span className="block text-slate-600">The agent may offer a substitute when a product is short. Substitutes always wait for your approval.</span>
          </span>
        </label>

        {note && (
          <p role={note.tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 text-sm ${note.tone === "ok" ? "text-auto" : "text-human"}`}>
            <Icon name={note.tone === "ok" ? "check" : "alert"} className="mt-0.5" />{note.text}
          </p>
        )}
        <button disabled={saving} className="btn-primary"><Icon name="key" />{saving ? "Signing" : "Sign new version"}</button>
      </form>
    </div>
  );
}
