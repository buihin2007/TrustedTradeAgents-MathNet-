import type { AuditDecision, RequestStatus } from "@/lib/types";

type Tone = "auto" | "human" | "blocked" | "neutral";
const TONE: Record<string, Tone> = {
  placed: "auto", buy_auto: "auto", approved: "auto",
  asking: "human", held: "human", ask_user: "human", approval_required: "human",
  blocked: "blocked", rejected: "blocked", failed: "blocked",
};
const CLASS: Record<Tone, string> = {
  auto: "bg-auto/10 text-auto border-auto/30",
  human: "bg-human/10 text-human border-human/30",
  blocked: "bg-blocked text-white border-blocked",
  neutral: "bg-neutral-100 text-neutral-600 border-neutral-200",
};

export function Status({ value }: { value: RequestStatus | AuditDecision | string }) {
  const tone = TONE[value] ?? "neutral";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${CLASS[tone]}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
