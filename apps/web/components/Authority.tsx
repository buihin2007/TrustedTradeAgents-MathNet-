import Link from "next/link";
import { Icon, type IconName } from "@/components/Icon";
import { eurShort } from "@/lib/labels";
import type { Money } from "@/lib/types";

const ZONES: { tone: string; icon: IconName; title: string }[] = [
  { tone: "border-auto/30 bg-auto/5 text-auto", icon: "bot", title: "Agent buys alone" },
  { tone: "border-human/30 bg-human/5 text-human", icon: "user", title: "Waits for you" },
  { tone: "border-blocked bg-blocked text-white", icon: "ban", title: "Gateway blocks" },
];

/** The three outcomes of the mandate as money ranges, so the buyer sees what the agent may do before asking. */
export function Authority({ auto, limit, weekly, link = true }: { auto: Money; limit: Money; weekly?: Money; link?: boolean }) {
  const ranges = [`up to ${eurShort(auto)}`, `${eurShort(auto)} to ${eurShort(limit)}`, `above ${eurShort(limit)}`];
  return (
    <section aria-label="What the agent may do" className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-medium text-slate-700">What the agent may do per order</h2>
        {link && (
          <Link href="/mandate" className="text-sm text-auto hover:underline">Change mandate</Link>
        )}
      </div>
      <div className="grid grid-cols-3 gap-2">
        {ZONES.map((z, i) => (
          <div key={z.title} className={`rounded-lg border px-3 py-2 ${z.tone}`}>
            <p className="flex items-center gap-1.5 text-xs font-medium"><Icon name={z.icon} className="h-3.5 w-3.5" />{z.title}</p>
            <p className="mt-0.5 font-mono text-sm tabular-nums">{ranges[i]}</p>
          </div>
        ))}
      </div>
      {weekly != null && <p className="text-xs text-slate-600">Weekly budget {eurShort(weekly)}. Every order is checked at the Valio Aimo gateway, not in this app.</p>}
    </section>
  );
}
