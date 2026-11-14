import { eur } from "@/lib/api";
import type { PricedOption } from "@/lib/types";

const TITLE: Record<string, string> = {
  best_match: "Best match", cheapest: "Cheapest", most_reliable: "Most reliable", split_or_substitute: "Split or substitute",
};

export function OptionCard({ option, chosen, action }: { option: PricedOption; chosen?: boolean; action?: React.ReactNode }) {
  return (
    <div className={`rounded-lg border bg-white p-4 ${chosen ? "border-auto ring-2 ring-auto/30" : "border-neutral-200"}`}>
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="font-semibold">{TITLE[option.label] ?? option.label}</h3>
        <span className="text-lg font-semibold">{eur(option.total_eur)}</span>
      </div>
      <ul className="space-y-1 text-sm">
        {option.lines.map((l) => (
          <li key={`${l.product_id}-${l.substitute_for ?? ""}`} className="flex justify-between gap-2">
            <span>
              {l.quantity_units} × {l.name_en ?? `#${l.product_id}`}
              {l.substitute_for && <span className="ml-1 rounded bg-human/10 px-1 text-xs text-human">substitute</span>}
              {l.available_units != null && l.available_units < l.quantity_units && (
                <span className="ml-1 rounded bg-human/10 px-1 text-xs text-human">only {l.available_units} available</span>
              )}
            </span>
            <span className="whitespace-nowrap text-neutral-500">{eur(l.line_total_eur)}</span>
          </li>
        ))}
      </ul>
      {option.explanation && <p className="mt-3 text-sm text-neutral-600">{option.explanation}</p>}
      {option.reasons.length > 0 && (
        <p className="mt-2 text-xs text-human">Needs approval: {option.reasons.join(", ")}</p>
      )}
      {action && <div className="mt-3">{action}</div>}
    </div>
  );
}
