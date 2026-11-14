"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

const LINKS = [
  { href: "/requests", label: "Requests" },
  { href: "/approvals", label: "Approvals" },
  { href: "/mandate", label: "Mandate" },
  { href: "/audit", label: "Audit trail" },
];

export function Nav({ email }: { email?: string }) {
  const path = usePathname();
  const router = useRouter();
  const signOut = async () => {
    await createClient().auth.signOut();
    router.replace("/login");
  };
  return (
    <header className="border-b border-neutral-200 bg-white">
      <nav className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3 text-sm">
        <span className="font-semibold">Trade Gateway</span>
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href}
            className={path.startsWith(l.href) ? "font-medium text-auto" : "text-neutral-600 hover:text-neutral-900"}>
            {l.label}
          </Link>
        ))}
        <span className="ml-auto text-neutral-500">{email}</span>
        <button onClick={signOut} className="text-neutral-600 hover:text-neutral-900">Sign out</button>
      </nav>
    </header>
  );
}
