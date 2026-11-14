/**
 * JSON shapes the web app expects from services/api (owner P3). Field names follow tta_contracts.
 * Money comes as string (Pydantic Decimal) or number; format with eur().
 */
export type Money = string | number;
export type RequestStatus = "running" | "asking" | "held" | "placed" | "blocked" | "rejected" | "failed";
export type OptionLabel = "best_match" | "cheapest" | "most_reliable" | "split_or_substitute";
export type AuditDecision = "buy_auto" | "ask_user" | "approval_required" | "approved" | "rejected" | "blocked" | "none";

export interface RequestSummary {
  id: string;
  text: string;
  status: RequestStatus;
  step: string | null;          // e.g. "parsing", "searching", "pricing", "building_options"
  created_at: string;
}

export interface PricedLine {
  product_id: number;
  name_en: string | null;
  quantity_units: number;
  unit_price_eur: Money | null;   // null on previews (not yet priced by the gateway)
  line_total_eur: Money | null;
  available_units: number | null;
  substitute_for: number | null;
}

export interface PricedOption {
  label: OptionLabel;
  lines: PricedLine[];
  total_eur: Money;
  explanation: string | null;
  reasons: string[];
}

/** Built by the buyer API before place_order; normalise with toPriced() before rendering. */
export interface OptionPreview {
  option: { label: OptionLabel; lines: { product_id: number; quantity_units: number; substitute_for: number | null }[]; explanation: string | null };
  total_eur: Money;
  supplied_quantity: Money;
  available: boolean;
}

export interface Order {
  order_id: string;
  intent_id: string;
  status: "placed" | "held" | "blocked" | "rejected";
  reasons: string[];
  options: PricedOption[];
  chosen_index: number | null;
  total_eur: Money | null;
  delivery_date: string;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  trace_id: string | null;
}

export interface Question {
  id: string;
  question: string;
  choices: string[];
  answer: string | null;
  asked_at: string;
  answered_at: string | null;
}

export interface RequestDetail extends RequestSummary {
  parsed: Record<string, unknown> | null;   // ParsedRequest from the LLM: shown as chips
  options: (PricedOption | OptionPreview)[];
  questions: Question[];                     // the open one has answer = null (status "asking")
  order: Order | null;
  error: string | null;
  trace_id: string | null;
}

/** GET /api/approvals returns held Orders. */
export type Approval = Order;

export interface MandateView {
  auto_limit_eur: Money;
  order_limit_eur: Money;
  weekly_budget_eur: Money;
  price_tolerance_pct: Money;
  allow_substitution: boolean;
  allowed_category_codes: string[] | null;
  mandate_id: string;
  agent_id: string;
  version: number;
  signed_by: string;
  signed_at: string;
  agent_status?: "active" | "paused";       // not returned by the API yet; the page tracks it locally
}

export interface AuditEvent {
  seq: number;
  ts: string;
  request_id: string | null;
  actor_type: "user" | "agent" | "llm" | "gateway" | "system";
  event_type: string;
  decision: AuditDecision;
  summary: string;
  trace_id: string | null;
}
