import { Icon } from "@/components/Icon";
import { BLOCKING, reasonLabel } from "@/lib/labels";

/** Gateway reason codes as readable chips; the raw code is kept for the audit-minded reader. */
export function Reasons({ codes, end = false }: { codes: string[]; end?: boolean }) {
  return (
    <ul className={`flex flex-wrap gap-1.5 ${end ? "justify-end" : ""}`}>
      {[...new Set(codes)].map((code) => {
        const blocks = BLOCKING.has(code);
        return (
          <li key={code} title={code}
            className={`chip ${blocks ? "border-blocked bg-blocked text-white" : "border-human/30 bg-human/10 text-human"}`}>
            <Icon name={blocks ? "ban" : "user"} className="h-3 w-3" />
            {reasonLabel(code)}
            <span className={`font-mono text-[10px] ${blocks ? "text-white/70" : "text-human/70"}`}>{code}</span>
          </li>
        );
      })}
    </ul>
  );
}
