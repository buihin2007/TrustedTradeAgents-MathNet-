"use client";
import Link from "next/link";
import { useState } from "react";
import { Icon, Spinner } from "@/components/Icon";
import { eur } from "@/lib/api";
import { day } from "@/lib/labels";
import type { AskItem, Basket, BasketsItem, DishesItem, SayItem, StepItem } from "@/lib/types";

/** The buyer's own words, right-aligned like a chat message. */
export function UserBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-white shadow-sm">{children}</div>
    </div>
  );
}

/** Marks a block as coming from the agent. */
function AgentRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span aria-hidden className="mt-1 hidden h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-700 sm:flex">
        <Icon name="bot" className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** A run of agent steps, shown the way Claude shows its thinking: live while running, collapsible after. */
export function Thinking({ steps }: { steps: StepItem[] }) {
  const [open, setOpen] = useState(true);
  const running = steps.find((s) => s.status === "run");
  const failed = steps.some((s) => s.status === "fail");
  const seconds = (steps.reduce((sum, s) => sum + (s.ms ?? 0), 0) / 1000).toFixed(1);
  return (
    <AgentRow>
      <div className="card px-4 py-3 text-sm">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}
          className="flex min-h-8 w-full items-center gap-2 text-left text-slate-700">
          {running ? <Spinner /> : failed ? <Icon name="alert" className="h-4 w-4 text-human" /> : <Icon name="check" className="h-4 w-4 text-auto" />}
          <span className="font-medium" aria-live="polite">{running ? `${running.title}…` : `Worked for ${seconds} s · ${steps.length} ${steps.length === 1 ? "step" : "steps"}`}</span>
          <Icon name="chevron" className={`ml-auto h-4 w-4 text-slate-500 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
          <span className="sr-only">{open ? "Hide steps" : "Show steps"}</span>
        </button>
        {open && (
          <ol className="mt-3 space-y-2.5 border-l-2 border-slate-100 pl-4">
            {steps.map((s, i) => (
              <li key={i}>
                <div className="flex items-baseline gap-2">
                  <span className="flex w-4 shrink-0 justify-center self-center">
                    {s.status === "run" ? <Spinner className="h-3.5 w-3.5" />
                      : s.status === "fail" ? <Icon name="x" className="h-3.5 w-3.5 text-human" />
                      : <Icon name="check" className="h-3.5 w-3.5 text-auto" />}
                  </span>
                  <span className={s.status === "run" ? "font-medium text-slate-900" : "text-slate-800"}>{s.title}</span>
                  {s.ms != null && <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-slate-500">{(s.ms / 1000).toFixed(1)} s</span>}
                </div>
                {s.detail && <p className="ml-6 text-slate-600">{s.detail}</p>}
                {s.items.length > 0 && (
                  <ul className="ml-6 mt-1 space-y-0.5 text-xs text-slate-600">
                    {s.items.slice(0, 8).map((line, n) => <li key={n} className="before:mr-1.5 before:text-slate-400 before:content-['·']">{line}</li>)}
                    {s.items.length > 8 && <li className="text-slate-500">and {s.items.length - 8} more</li>}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </AgentRow>
  );
}

/** One question at a time: options to click, plus a free-text field for anything else. */
export function Question({ q, busy, onAnswer }: { q: AskItem; busy: boolean; onAnswer: (q: AskItem, values: string[], other: string) => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");
  if (q.answer != null) {
    return (
      <div className="space-y-2">
        <AgentRow>
          <p className="text-sm text-slate-600">
            <span className="chip mr-2 border-slate-200 bg-slate-100 text-slate-700">{q.header}</span>
            {q.text}
          </p>
        </AgentRow>
        <UserBubble>{q.answer}</UserBubble>
      </div>
    );
  }
  const toggle = (value: string) => setPicked(picked.includes(value) ? picked.filter((v) => v !== value) : [...picked, value]);
  const inputId = `other-${q.id}`;
  return (
    <AgentRow>
      <div data-testid="question" data-field={q.field} role="group" aria-labelledby={`q-${q.id}`}
        className="card border-human/40 p-4 shadow-sm ring-4 ring-human/5">
        <div className="mb-2 flex items-center gap-2">
          <span className="chip border-human/30 bg-human/10 text-human"><Icon name="user" className="h-3 w-3" />{q.header}</span>
          {q.multi && <span className="text-xs text-slate-600">Select all that apply</span>}
        </div>
        <p id={`q-${q.id}`} className="mb-3 font-medium text-slate-900">{q.text}</p>
        <div className="space-y-2">
          {q.options.map((o) => {
            const on = picked.includes(o.value);
            return (
              <button key={o.value} type="button" disabled={busy} data-testid="option" data-value={o.value}
                aria-pressed={q.multi ? on : undefined}
                onClick={() => (q.multi ? toggle(o.value) : onAnswer(q, [o.value], ""))}
                className={`flex min-h-11 w-full items-center gap-3 rounded-lg border px-3 py-2 text-left text-sm transition-colors duration-200 hover:border-human/60 hover:bg-human/5 disabled:opacity-60 ${on ? "border-human bg-human/5" : "border-slate-200"}`}>
                {q.multi && (
                  <span aria-hidden className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border ${on ? "border-human bg-human text-white" : "border-slate-400"}`}>
                    {on && <Icon name="check" className="h-3 w-3" />}
                  </span>
                )}
                <span className="min-w-0">
                  <span className="font-medium text-slate-900">{o.label}</span>
                  {o.desc && <span className="block text-xs text-slate-600">{o.desc}</span>}
                </span>
              </button>
            );
          })}
        </div>
        <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (other.trim() || picked.length) onAnswer(q, picked, other.trim()); }}>
          <label htmlFor={inputId} className="sr-only">Other answer</label>
          <input id={inputId} className="field min-w-0 flex-1" maxLength={500} disabled={busy}
            placeholder={q.hint || "Something else"} value={other} onChange={(e) => setOther(e.target.value)} />
          <button data-testid="send" disabled={busy || (!other.trim() && !picked.length)} className="btn-human">
            {busy ? <Spinner className="h-4 w-4 border-white/40 border-t-white" /> : <Icon name="send" />}
            {busy ? "Sending" : q.multi && picked.length && !other.trim() ? `Confirm ${picked.length}` : "Send"}
          </button>
        </form>
      </div>
    </AgentRow>
  );
}

const SAY = {
  ok: { box: "border-auto/30 bg-auto/5", icon: "check", color: "text-auto" },
  warn: { box: "border-human/40 bg-human/5", icon: "alert", color: "text-human" },
  error: { box: "border-blocked/60 bg-white", icon: "ban", color: "text-blocked" },
  info: { box: "border-slate-200 bg-white", icon: "info", color: "text-slate-500" },
} as const;

export function Say({ item }: { item: SayItem }) {
  const s = SAY[item.tone] ?? SAY.info;
  return (
    <AgentRow>
      <div role={item.tone === "error" ? "alert" : undefined} className={`flex gap-3 rounded-xl border px-4 py-3 ${s.box}`}>
        <Icon name={s.icon} className={`mt-0.5 h-4 w-4 ${s.color}`} />
        <p className="min-w-0 whitespace-pre-wrap text-slate-900">{item.text}</p>
      </div>
    </AgentRow>
  );
}

/** Dishes the agent suggests, each checked against Valio Aimo's stock for the delivery date. */
export function Dishes({ item }: { item: DishesItem }) {
  return (
    <AgentRow>
      <div className="space-y-2">
        <p className="flex items-center gap-1.5 text-sm text-slate-600">
          <Icon name="utensils" className="h-4 w-4" />
          Suggestions for {item.headcount} people, checked against Valio Aimo stock for {day(item.date)}
        </p>
        <div className="grid gap-3 md:grid-cols-3">
          {item.dishes.map((d) => {
            const full = d.covered === d.total;
            const share = d.total ? Math.round((d.covered / d.total) * 100) : 0;
            return (
              <div key={d.name} className="card flex flex-col p-4 text-sm">
                <h3 className="font-semibold text-slate-900">{d.name}</h3>
                {d.why && <p className="mt-0.5 text-slate-600">{d.why}</p>}
                <div className="mt-3">
                  <div className="mb-1 flex justify-between text-xs">
                    <span className={full ? "text-auto" : "text-human"}>{d.covered} of {d.total} ingredients in stock</span>
                    {d.per_person && <span className="font-mono tabular-nums text-slate-700">{eur(d.per_person)} / person</span>}
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100" role="img" aria-label={`${share}% of ingredients in stock`}>
                    <div className={`h-full rounded-full ${full ? "bg-auto" : "bg-human"}`} style={{ width: `${share}%` }} />
                  </div>
                </div>
                <ul className="mt-3 space-y-0.5 text-xs text-slate-700">
                  {d.ingredients.map((line) => <li key={line}>{line} per person</li>)}
                </ul>
                {d.missing.length > 0 && <p className="mt-2 text-xs text-human">Not available that day: {d.missing.join(", ")}</p>}
                {d.swaps.map((swap) => (
                  <p key={swap} className="mt-1 flex items-start gap-1 text-xs text-human"><Icon name="swap" className="mt-0.5 h-3 w-3" />{swap}</p>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </AgentRow>
  );
}

const GATE: Record<Basket["gate"], { cls: string; icon: "bot" | "user" | "ban" | "info" }> = {
  auto: { cls: "border-auto/30 bg-auto/10 text-auto", icon: "bot" },
  approval: { cls: "border-human/30 bg-human/10 text-human", icon: "user" },
  blocked: { cls: "border-blocked bg-blocked text-white", icon: "ban" },
  unknown: { cls: "border-slate-200 bg-slate-50 text-slate-700", icon: "info" },
};

export function Baskets({ item, chosen }: { item: BasketsItem; chosen?: number | "cancel" }) {
  return (
    <AgentRow>
      <div className="space-y-2">
        <p className="flex items-center gap-1.5 text-sm text-slate-600">
          <Icon name="calendar" className="h-4 w-4" />
          Baskets for delivery on {day(item.date)}. Prices and stock come from the Valio Aimo gateway.
        </p>
        <div className="grid gap-3 lg:grid-cols-2">
          {item.baskets.map((b, index) => {
            const gate = GATE[b.gate] ?? GATE.unknown;
            const isChosen = chosen === index;
            return (
              <div key={b.label} className={`card p-4 text-sm ${isChosen ? "border-auto ring-2 ring-auto/30" : ""}`}>
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="flex items-center gap-2 font-semibold text-slate-900">
                    {b.title}
                    {isChosen && <span className="chip border-auto/30 bg-auto/10 text-auto"><Icon name="check" className="h-3 w-3" />Chosen</span>}
                  </h3>
                  <span className="font-mono text-lg font-medium tabular-nums text-slate-900">{eur(b.total)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <span className={`chip ${gate.cls}`}><Icon name={gate.icon} className="h-3 w-3" />{b.gate_note}</span>
                  {b.reliability != null && (
                    <span className="chip border-slate-200 bg-slate-50 text-slate-700">
                      <Icon name="truck" className="h-3 w-3" />{Math.round(b.reliability * 100)}% delivered on time
                    </span>
                  )}
                  {b.co2_kg != null && (
                    <span className="chip border-slate-200 bg-slate-50 text-slate-700" title="Category-level average from Valio Aimo data">
                      <Icon name="leaf" className="h-3 w-3" />≈ {b.co2_kg.toLocaleString("en-GB")} kg CO₂e
                    </span>
                  )}
                </div>
                <ul className="mt-3 divide-y divide-slate-100">
                  {b.lines.map((l, n) => (
                    <li key={`${l.product_id}-${n}`} className="py-2">
                      <div className="flex justify-between gap-3">
                        <span className="min-w-0 text-slate-900">
                          <span className="font-mono tabular-nums">{l.units}</span> × {l.name}
                          {l.substitute_for != null && (
                            <span className="chip ml-1.5 border-human/30 bg-human/10 text-human"><Icon name="swap" className="h-3 w-3" />Valio substitute</span>
                          )}
                        </span>
                        <span className="shrink-0 font-mono tabular-nums text-slate-700">{eur(l.line_total)}</span>
                      </div>
                      <p className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-xs text-slate-600">
                        <span>{l.pack} each</span>
                        {l.origin && <span className="inline-flex items-center gap-0.5"><Icon name="pin" className="h-3 w-3" />{l.origin}</span>}
                        <span>· for {l.amount} {l.need}</span>
                        {l.dishes.length > 0 && <span>· {l.dishes.join(", ")}</span>}
                      </p>
                      {l.note && <p className="mt-0.5 text-xs text-human">{l.note}</p>}
                    </li>
                  ))}
                </ul>
                {b.missing.length > 0 && (
                  <div className="mt-2 space-y-1 rounded-lg border border-human/20 bg-human/5 p-2.5 text-xs text-human">
                    {b.missing.map((m) => (
                      <p key={m.need} className="flex items-start gap-1.5">
                        <Icon name="alert" className="mt-0.5 h-3 w-3" />
                        <span><span className="font-medium">{m.need}:</span> {m.reason}{m.earliest && `, available from ${day(m.earliest)}`}</span>
                      </p>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {item.pantry.length > 0 && <p className="text-xs text-slate-600">Assumed to be in your kitchen already: {item.pantry.join(", ")}.</p>}
      </div>
    </AgentRow>
  );
}

export function OrderLink({ status }: { status: string }) {
  if (status !== "held") return null;
  return (
    <AgentRow>
      <Link href="/approvals" className="btn-human"><Icon name="inbox" />Review in Approvals</Link>
    </AgentRow>
  );
}
