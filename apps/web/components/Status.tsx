import { Icon, Spinner } from "@/components/Icon";
import { statusLabel, TONE, type Tone } from "@/lib/labels";
import type { AuditDecision, RequestStatus } from "@/lib/types";

const CLASS: Record<Tone, string> = {
  auto: "border-auto/30 bg-auto/10 text-auto",
  human: "border-human/30 bg-human/10 text-human",
  blocked: "border-blocked bg-blocked text-white",
  neutral: "border-slate-200 bg-slate-100 text-slate-700",
};
const ICON = { auto: "check", human: "user", blocked: "ban" } as const;

/** One badge for request status and audit decisions. data-status carries the raw value for tests and the demo recorder. */
export function Status({ value }: { value: RequestStatus | AuditDecision | string }) {
  const tone = TONE[value] ?? "neutral";
  return (
    <span data-status={value} title={value} className={`chip ${CLASS[tone]}`}>
      {value === "running" ? <Spinner className="h-3 w-3" /> : tone !== "neutral" && <Icon name={ICON[tone]} className="h-3 w-3" />}
      {statusLabel(value)}
    </span>
  );
}
