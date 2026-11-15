import { Icon } from "@/components/Icon";
import { Reasons } from "@/components/Reasons";
import { eur } from "@/lib/api";
import type { PricedOption } from "@/lib/types";

const TITLE: Record<string, string> = {
  best_match: "Best match", cheapest: "Cheapest", most_reliable: "Most reliable", split_or_substitute: "Split or substitute",
};

export function OptionCard({ option, chosen, action }: { option: PricedOption; chosen?: boolean; action?: React.ReactNode }) {
  return (
    <div className={`card flex flex-col p-4 ${chosen ? "border-auto ring-2 ring-auto/30" : ""}`}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h3 className="font-semibold text-slate-900">{TITLE[option.label] ?? option.label}</h3>
        <span className="font-mono text-lg font-medium tabular-nums text-slate-900">{eur(option.total_eur)}</span>
      </div>
      <ul className="divide-y divide-slate-100 text-sm">
        {option.lines.map((l) => (
          <li key={`${l.product_id}-${l.substitute_for ?? ""}`} className="flex justify-between gap-3 py-1.5">
            <span className="min-w-0 text-slate-800">
              <span className="font-mono tabular-nums">{l.quantity_units}</span> × {l.name_en ?? `#${l.product_id}`}
              {l.substitute_for && (
                <span className="chip ml-1.5 border-human/30 bg-human/10 text-human"><Icon name="swap" className="h-3 w-3" />substitute</span>
              )}
              {l.available_units != null && l.available_units < l.quantity_units && (
                <span className="chip ml-1.5 border-human/30 bg-human/10 text-human">only {l.available_units} available</span>
              )}
            </span>
            {l.line_total_eur != null && <span className="shrink-0 font-mono tabular-nums text-slate-600">{eur(l.line_total_eur)}</span>}
          </li>
        ))}
      </ul>
      {option.explanation && <p className="mt-3 text-sm text-slate-600">{option.explanation}</p>}
      {option.reasons.length > 0 && <div className="mt-3"><Reasons codes={option.reasons} /></div>}
      {action && <div className="mt-auto pt-4">{action}</div>}
    </div>
  );
}
