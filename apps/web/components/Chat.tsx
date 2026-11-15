"use client";
import Link from "next/link";
import { useState } from "react";
import { eur } from "@/lib/api";
import type { AskItem, Basket, BasketsItem, DishesItem, SayItem, StepItem } from "@/lib/types";

const day = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-neutral-300 border-t-auto" />;
}

/** The buyer's own words, right-aligned like a chat message. */
export function UserBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-auto px-4 py-2 text-white">{children}</div>
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
    <div className="rounded-xl border border-neutral-200 bg-white px-4 py-3 text-sm">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-left text-neutral-600">
        {running ? <Spinner /> : <span className={failed ? "text-human" : "text-auto"}>{failed ? "!" : "✓"}</span>}
        <span className="font-medium">{running ? `${running.title}…` : `Worked for ${seconds} s · ${steps.length} steps`}</span>
        <span className="ml-auto text-xs text-neutral-400">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <ol className="mt-3 space-y-2 border-l border-neutral-200 pl-4">
          {steps.map((s, i) => (
            <li key={i}>
              <div className="flex items-baseline gap-2">
                <span className="w-3 shrink-0 text-xs">
                  {s.status === "run" ? <Spinner /> : s.status === "fail" ? <span className="text-human">✕</span> : <span className="text-auto">✓</span>}
                </span>
                <span className={s.status === "run" ? "font-medium" : "text-neutral-800"}>{s.title}</span>
                {s.ms != null && <span className="ml-auto shrink-0 text-xs tabular-nums text-neutral-400">{(s.ms / 1000).toFixed(1)} s</span>}
              </div>
              {s.detail && <p className="ml-5 text-neutral-500">{s.detail}</p>}
              {s.items.length > 0 && (
                <ul className="ml-5 mt-1 space-y-0.5 text-xs text-neutral-500">
                  {s.items.slice(0, 8).map((line, n) => <li key={n}>· {line}</li>)}
                  {s.items.length > 8 && <li>· and {s.items.length - 8} more</li>}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/** One question at a time: options to click, plus a free-text field for anything else. */
export function Question({ q, busy, onAnswer }: { q: AskItem; busy: boolean; onAnswer: (q: AskItem, values: string[], other: string) => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState("");
  if (q.answer != null) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-neutral-500">
          <span className="mr-2 rounded-full bg-neutral-100 px-2 py-0.5 text-xs font-medium">{q.header}</span>
          {q.text}
        </p>
        <UserBubble>{q.answer}</UserBubble>
      </div>
    );
  }
  const toggle = (value: string) => setPicked(picked.includes(value) ? picked.filter((v) => v !== value) : [...picked, value]);
  return (
    <div data-testid="question" data-field={q.field} className="rounded-xl border border-human/40 bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full bg-human/10 px-2 py-0.5 text-xs font-medium text-human">{q.header}</span>
        {q.multi && <span className="text-xs text-neutral-500">Select all that apply</span>}
      </div>
      <p className="mb-3 font-medium">{q.text}</p>
      <div className="space-y-2">
        {q.options.map((o) => {
          const on = picked.includes(o.value);
          return (
            <button key={o.value} type="button" disabled={busy} data-testid="option" data-value={o.value}
              onClick={() => (q.multi ? toggle(o.value) : onAnswer(q, [o.value], ""))}
              className={`flex w-full items-start gap-3 rounded-lg border px-3 py-2 text-left text-sm transition hover:border-human/60 hover:bg-human/5 disabled:opacity-60 ${on ? "border-human bg-human/5" : "border-neutral-200"}`}>
              {q.multi && (
                <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${on ? "border-human bg-human text-white" : "border-neutral-300"}`}>
                  {on ? "✓" : ""}
                </span>
              )}
              <span>
                <span className="font-medium">{o.label}</span>
                {o.desc && <span className="block text-xs text-neutral-500">{o.desc}</span>}
              </span>
            </button>
          );
        })}
      </div>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (other.trim() || picked.length) onAnswer(q, picked, other.trim()); }}>
        <input className="min-w-0 flex-1 rounded-lg border border-neutral-300 px-3 py-2 text-sm" maxLength={500} disabled={busy}
          placeholder={q.hint || "Something else"} value={other} onChange={(e) => setOther(e.target.value)} />
        <button data-testid="send" disabled={busy || (!other.trim() && !picked.length)} className="rounded-lg bg-human px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {busy ? "Sending" : q.multi && picked.length && !other.trim() ? `Confirm ${picked.length}` : "Send"}
        </button>
      </form>
    </div>
  );
}

export function Say({ item }: { item: SayItem }) {
  const tone = {
    ok: "border-auto/30 bg-auto/5",
    warn: "border-human/40 bg-human/5",
    error: "border-blocked bg-white",
    info: "border-neutral-200 bg-white",
  }[item.tone];
  return <div className={`whitespace-pre-wrap rounded-xl border px-4 py-3 ${tone}`}>{item.text}</div>;
}

/** Dishes the agent suggests, each checked against Valio Aimo's stock for the delivery date. */
export function Dishes({ item }: { item: DishesItem }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-neutral-500">Suggestions for {item.headcount} people, checked against Valio Aimo stock for {day(item.date)}</p>
      <div className="grid gap-3 md:grid-cols-3">
        {item.dishes.map((d) => {
          const full = d.covered === d.total;
          return (
            <div key={d.name} className="rounded-xl border border-neutral-200 bg-white p-4 text-sm">
              <h3 className="font-semibold">{d.name}</h3>
              {d.why && <p className="text-neutral-500">{d.why}</p>}
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                <span className={`rounded-full border px-2 py-0.5 ${full ? "border-auto/30 bg-auto/10 text-auto" : "border-human/30 bg-human/10 text-human"}`}>
                  {d.covered}/{d.total} in stock
                </span>
                {d.per_person && <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5">{eur(d.per_person)} per person</span>}
              </div>
              <ul className="mt-2 space-y-0.5 text-xs text-neutral-600">
                {d.ingredients.map((line) => <li key={line}>{line} per person</li>)}
              </ul>
              {d.missing.length > 0 && <p className="mt-2 text-xs text-human">Not available that day: {d.missing.join(", ")}</p>}
              {d.swaps.map((swap) => <p key={swap} className="mt-1 text-xs text-human">{swap}</p>)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const GATE: Record<Basket["gate"], string> = {
  auto: "border-auto/30 bg-auto/10 text-auto",
  approval: "border-human/30 bg-human/10 text-human",
  blocked: "border-blocked bg-blocked text-white",
  unknown: "border-neutral-200 bg-neutral-50 text-neutral-600",
};

export function Baskets({ item, chosen }: { item: BasketsItem; chosen?: number | "cancel" }) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-neutral-500">Baskets for delivery on {day(item.date)}. Prices and stock come from the Valio Aimo gateway.</p>
      <div className="grid gap-3 lg:grid-cols-2">
        {item.baskets.map((b, index) => (
          <div key={b.label} className={`rounded-xl border bg-white p-4 text-sm ${chosen === index ? "border-auto ring-2 ring-auto/30" : "border-neutral-200"}`}>
            <div className="flex items-baseline justify-between gap-3">
              <h3 className="font-semibold">{b.title}</h3>
              <span className="text-lg font-semibold tabular-nums">{eur(b.total)}</span>
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5 text-xs">
              <span className={`rounded-full border px-2 py-0.5 ${GATE[b.gate]}`}>{b.gate_note}</span>
              {b.reliability != null && (
                <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5">
                  {Math.round(b.reliability * 100)}% delivery reliability
                </span>
              )}
              {b.co2_kg != null && (
                <span className="rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5" title="Category-level average from Valio Aimo data">
                  ≈ {b.co2_kg.toLocaleString("en-GB")} kg CO₂e
                </span>
              )}
            </div>
            <ul className="mt-3 divide-y divide-neutral-100">
              {b.lines.map((l, n) => (
                <li key={`${l.product_id}-${n}`} className="py-1.5">
                  <div className="flex justify-between gap-3">
                    <span>
                      <span className="tabular-nums">{l.units}</span> × {l.name}
                      <span className="text-neutral-400"> ({l.pack} each{l.origin ? `, ${l.origin}` : ""})</span>
                      {l.substitute_for != null && <span className="ml-1 rounded bg-human/10 px-1 text-xs text-human">Valio substitute</span>}
                    </span>
                    <span className="shrink-0 tabular-nums text-neutral-600">{eur(l.line_total)}</span>
                  </div>
                  <p className="text-xs text-neutral-500">
                    for {l.amount} {l.need}
                    {l.dishes.length > 0 && ` · ${l.dishes.join(", ")}`}
                  </p>
                  {l.note && <p className="text-xs text-human">{l.note}</p>}
                </li>
              ))}
            </ul>
            {b.missing.length > 0 && (
              <div className="mt-2 rounded-lg bg-human/5 p-2 text-xs text-human">
                {b.missing.map((m) => (
                  <p key={m.need}>
                    {m.need}: {m.reason}
                    {m.earliest && `, available from ${day(m.earliest)}`}
                  </p>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {item.pantry.length > 0 && <p className="text-xs text-neutral-500">Assumed to be in your kitchen already: {item.pantry.join(", ")}.</p>}
    </div>
  );
}

export function OrderLink({ status }: { status: string }) {
  if (status !== "held") return null;
  return <Link href="/approvals" className="inline-block rounded-lg bg-human px-4 py-2 text-sm font-medium text-white">Review in Approvals</Link>;
}
