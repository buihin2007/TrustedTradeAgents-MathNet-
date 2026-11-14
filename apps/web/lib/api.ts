"use client";
import type { OptionPreview, PricedOption } from "./types";
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
