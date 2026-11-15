"use client";
import Link from "next/link";
import { use, useEffect, useRef, useState } from "react";
import { Baskets, Dishes, OrderLink, Question, Say, Thinking, UserBubble } from "@/components/Chat";
import { Status } from "@/components/Status";
import { api, useRequestLive } from "@/lib/api";
import type { AskItem, FeedItem, StepItem } from "@/lib/types";

type Block = { kind: "steps"; steps: StepItem[] } | { kind: "item"; item: Exclude<FeedItem, StepItem> };

/** Consecutive steps become one "thinking" block; everything else is shown on its own. */
function blocks(feed: FeedItem[]): Block[] {
  const out: Block[] = [];
  for (const item of feed) {
    const last = out[out.length - 1];
    if (item.t === "step") {
      if (last?.kind === "steps") last.steps.push(item);
      else out.push({ kind: "steps", steps: [item] });
    } else out.push({ kind: "item", item });
  }
  return out;
}

export default function RequestPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: r, error } = useRequestLive(id);
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const feed = r?.parsed?.feed ?? [];
  const size = feed.length;
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [size, r?.status]);

  const answer = async (q: AskItem, values: string[], other: string) => {
    const labels = values.map((value) => q.options.find((o) => o.value === value)?.label ?? value);
    const shown = [...labels, other].filter(Boolean).join(", ").slice(0, 1000);
    setBusy(true);
    setSendError(null);
    try {
      await api(`/requests/${id}/answer`, {
        method: "POST",
        body: JSON.stringify({ question_id: q.id, answer: shown, values, other }),
      });
    } catch (e) {
      setSendError(e instanceof Error ? e.message : String(e));
    }
    setBusy(false);
  };

  if (!r) return <p className={error ? "text-human" : "text-neutral-500"}>{error ? `Could not load request: ${error}` : "Loading..."}</p>;
  const last = feed[feed.length - 1];
  const waiting = r.status === "running" && !(last?.t === "step" && last.status === "run");

  return (
    <div className="space-y-4 pb-24">
      <div className="flex items-center justify-between">
        <Link href="/requests" className="text-sm text-neutral-500">← Requests</Link>
        <Status value={r.status} />
      </div>
      <UserBubble>{r.text}</UserBubble>
      {blocks(feed).map((block, index) => {
        if (block.kind === "steps") return <Thinking key={index} steps={block.steps} />;
        const item = block.item;
        if (item.t === "ask") return <Question key={item.id} q={item} busy={busy} onAnswer={answer} />;
        if (item.t === "dishes") return <Dishes key={index} item={item} />;
        if (item.t === "baskets") return <Baskets key={index} item={item} chosen={r.parsed?.choice} />;
        return <Say key={index} item={item} />;
      })}
      {waiting && (
        <p className="flex items-center gap-2 text-sm text-neutral-500">
          <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-neutral-300 border-t-auto" />
          Thinking…
        </p>
      )}
      {r.parsed?.order && <OrderLink status={r.parsed.order.status} />}
      {!r.parsed && r.error && <Say item={{ t: "say", text: r.error, tone: "error" }} />}
      {sendError && <p className="text-sm text-human">{sendError}</p>}
      <div ref={bottom} />
    </div>
  );
}
