"use client";
import type { OptionPreview, PricedOption, RequestLive } from "./types";
import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";

/** Calls the buyer API on the same origin (/api/... behind nginx) with the user's Supabase JWT. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await createClient().auth.getSession();
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(data.session ? { Authorization: `Bearer ${data.session.access_token}` } : {}),
      ...init.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${body.slice(0, 200)}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

/** GET with optional polling. Returns data, error and a reload function. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);
  useEffect(() => {
    load();
    if (!pollMs) return;
    const t = setInterval(load, pollMs);
    return () => clearInterval(t);
  }, [load, pollMs]);
  return { data, error, reload: load };
}

export const eur = (v: string | number | null | undefined) =>
  v == null ? "-" : `${Number(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} EUR`;

/** Options come priced (from the gateway) or as previews (from the buyer API); render both the same way. */
export function toPriced(o: PricedOption | OptionPreview): PricedOption {
  if (!("option" in o)) return o;
  return {
    label: o.option.label,
    lines: o.option.lines.map((l) => ({ ...l, name_en: null, unit_price_eur: null, line_total_eur: null, available_units: null })),
    total_eur: o.total_eur,
    explanation: o.option.explanation,
    reasons: [],
  };
}

const FINAL = ["placed", "held", "blocked", "rejected", "failed"];

/**
 * Live view of one request: server-sent snapshots from /stream, with 1 s polling as a fallback
 * (dev proxy, old browser, dropped connection). Stops when the request reaches a final status.
 */
export function useRequestLive(id: string) {
  const [data, setData] = useState<RequestLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false;
    const controller = new AbortController();
    const finished = (status?: string) => !!status && FINAL.includes(status);
    const poll = async () => {
      while (!stopped) {
        try {
          const next = await api<RequestLive>(`/requests/${id}`);
          if (stopped) return;
          setData(next);
          setError(null);
          if (finished(next.status)) return;
        } catch (e) {
          if (!stopped) setError(e instanceof Error ? e.message : String(e));
        }
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    };
    (async () => {
      let last: RequestLive | null = null;
      try {
        const { data: auth } = await createClient().auth.getSession();
        const res = await fetch(`/api/requests/${id}/stream`, {
          headers: auth.session ? { Authorization: `Bearer ${auth.session.access_token}` } : {},
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(String(res.status));
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          // The server sends data or a keep-alive every 10 s: a silent stream is a stuck proxy, so poll instead.
          const { value, done } = await Promise.race([
            reader.read(),
            new Promise<never>((_, reject) => setTimeout(() => reject(new Error("stalled")), 25000)),
          ]);
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let cut = buffer.indexOf("\n\n");
          while (cut >= 0) {
            const line = buffer.slice(0, cut).split("\n").find((l) => l.startsWith("data: "));
            buffer = buffer.slice(cut + 2);
            if (line) {
              last = JSON.parse(line.slice(6)) as RequestLive;
              if (!stopped) {
                setData(last);
                setError(null);
              }
            }
            cut = buffer.indexOf("\n\n");
          }
        }
      } catch {
        controller.abort(); // fall through to polling
      }
      if (!stopped && !finished(last?.status)) await poll();
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [id]);
  return { data, error };
}
