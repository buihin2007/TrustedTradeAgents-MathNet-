"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Icon, type IconName } from "@/components/Icon";
import { useApi } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import type { Approval } from "@/lib/types";

const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: "/requests", label: "Requests", icon: "message" },
  { href: "/approvals", label: "Approvals", icon: "inbox" },
  { href: "/mandate", label: "Mandate", icon: "sliders" },
  { href: "/audit", label: "Audit trail", icon: "file" },
];

export function Nav({ email }: { email?: string }) {
  const path = usePathname();
  const router = useRouter();
  const { data: held } = useApi<Approval[]>("/approvals", 10000);
  const waiting = held?.length ?? 0;
  const signOut = async () => {
    await createClient().auth.signOut();
    router.replace("/login");
  };
  return (
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
      <nav aria-label="Main" className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-2 text-sm sm:px-6">
        <Link href="/requests" className="mr-3 flex items-center gap-2 font-semibold text-brand">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand text-white">
            <Icon name="shield" className="h-4 w-4" />
          </span>
          <span className="hidden sm:inline">Trade Gateway</span>
        </Link>
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto">
          {LINKS.map((l) => {
            const active = path.startsWith(l.href);
            return (
              <Link key={l.href} href={l.href} aria-current={active ? "page" : undefined}
                className={`flex min-h-9 items-center gap-1.5 whitespace-nowrap rounded-lg px-3 transition-colors duration-200 ${active ? "bg-slate-100 font-medium text-brand" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                <Icon name={l.icon} />
                {l.label}
                {l.href === "/approvals" && waiting > 0 && (
                  <span className="ml-0.5 rounded-full bg-human px-1.5 text-xs font-semibold text-white">
                    {waiting}<span className="sr-only"> waiting</span>
                  </span>
                )}
              </Link>
            );
          })}
        </div>
        <span className="ml-auto hidden truncate pl-3 text-slate-500 md:inline">{email}</span>
        <button onClick={signOut} title="Sign out"
          className="ml-2 flex min-h-9 items-center gap-1.5 rounded-lg px-3 text-slate-600 transition-colors duration-200 hover:bg-slate-50 hover:text-slate-900">
          <Icon name="logout" />
          <span className="hidden sm:inline">Sign out</span>
        </button>
      </nav>
    </header>
  );
}
