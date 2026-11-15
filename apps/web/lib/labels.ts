/** Plain-English labels for the codes the API and gateway return. The raw code stays visible as a tooltip. */

export type Tone = "auto" | "human" | "blocked" | "neutral";

export const TONE: Record<string, Tone> = {
  placed: "auto", buy_auto: "auto", approved: "auto",
  asking: "human", held: "human", ask_user: "human", approval_required: "human",
  blocked: "blocked", rejected: "blocked", failed: "blocked",
};

export const STATUS_LABEL: Record<string, string> = {
  running: "Working",
  asking: "Your answer needed",
  held: "Waiting for approval",
  placed: "Placed",
  blocked: "Blocked",
  rejected: "Rejected",
  failed: "Failed",
  buy_auto: "Bought automatically",
  ask_user: "Asked the buyer",
  approval_required: "Needs approval",
  approved: "Approved",
};

export const REASON_LABEL: Record<string, string> = {
  MUST_VIOLATED: "Breaks a must-have rule",
  OVER_ORDER_LIMIT: "Above the per-order limit",
  OVER_WEEKLY_BUDGET: "Over the weekly budget",
  LOT_SIZE: "Not a valid pack quantity",
  OVER_AUTO_LIMIT: "Above what the agent may buy alone",
  SUBSTITUTION: "Contains a substitute",
  PARTIAL_AVAILABILITY: "Not everything is in stock",
  PRICE_MOVED: "Price changed since the quote",
  JEV_BLOCK: "Safety check refused it",
  JEV_UNSURE: "Safety check was unsure",
  JEV_UNAVAILABLE: "Safety check was offline",
  FORBIDDEN: "Wrong identity for this action",
  AGENT_PAUSED: "The agent is paused",
  INTENT_INVALID: "The signed request expired",
  CAP_EXCEEDED: "Too many products asked for",
  RATE_LIMIT: "Too many calls in a short time",
};

/** Reasons that stop an order outright; the rest only hold it for a person. */
export const BLOCKING = new Set(["MUST_VIOLATED", "OVER_ORDER_LIMIT", "OVER_WEEKLY_BUDGET", "LOT_SIZE", "JEV_BLOCK", "FORBIDDEN", "AGENT_PAUSED", "INTENT_INVALID", "CAP_EXCEEDED", "RATE_LIMIT"]);

export const statusLabel = (value: string) => STATUS_LABEL[value] ?? value.replaceAll("_", " ");
export const reasonLabel = (code: string) => REASON_LABEL[code] ?? code.replaceAll("_", " ").toLowerCase();

/** Whole euros, for limits: 3,000 EUR. */
export const eurShort = (v: string | number | null | undefined) =>
  v == null || v === "" ? "-" : `${Number(v).toLocaleString("en-GB", { maximumFractionDigits: 0 })} EUR`;

export const day = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });

export function ago(iso: string) {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
